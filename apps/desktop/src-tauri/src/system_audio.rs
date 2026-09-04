//! System (desktop) audio capture through the `huddle-audio-tap` helper, which uses
//! ScreenCaptureKit. Requires the macOS "Screen & System Audio Recording" permission;
//! no virtual audio driver is involved.

use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;

#[derive(Serialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct SystemAudioSupport {
    pub supported: bool,        // helper present and macOS 14.2+
    pub permission: String,     // always "unknown": macOS exposes no query for it
    pub message: Option<String>,
}

fn helper_path() -> Option<PathBuf> {
    // Release: Tauri externalBin next to the executable. Dev: src-tauri/binaries/.
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let p = dir.join("huddle-audio-tap");
            if p.exists() {
                return Some(p);
            }
        }
    }
    let triple = format!("{}-{}", std::env::consts::ARCH, "apple-darwin");
    let dev = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("binaries").join(format!("huddle-audio-tap-{triple}"));
    if dev.exists() {
        return Some(dev);
    }
    None
}

/// Microphone permission as macOS records it for Huddle: "granted" | "denied" | "undetermined".
fn mic_state(arg: &str) -> String {
    let Some(helper) = helper_path() else { return "unknown".into() };
    match Command::new(helper).arg(arg).output() {
        Ok(o) => String::from_utf8_lossy(&o.stdout).trim().to_string(),
        Err(_) => "unknown".into(),
    }
}

#[tauri::command]
pub async fn microphone_permission() -> String {
    tauri::async_runtime::spawn_blocking(|| mic_state("mic-check")).await.unwrap_or_else(|_| "unknown".into())
}

/// Shows the macOS microphone prompt (once); returns the resulting state.
#[tauri::command]
pub async fn request_microphone_permission() -> String {
    tauri::async_runtime::spawn_blocking(|| mic_state("mic-request")).await.unwrap_or_else(|_| "unknown".into())
}

#[tauri::command]
pub fn open_microphone_settings() -> Result<(), String> {
    #[cfg(target_os = "macos")]
    std::process::Command::new("open")
        .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")
        .status()
        .map_err(|e| e.to_string())?;
    Ok(())
}

/// Whether system audio capture is available at all (helper present, macOS 14.2+). macOS has no
/// API for the "System Audio Recording Only" permission itself, so `permission` is "unknown";
/// the UI points to System Settings instead of pretending to know.
pub fn support() -> SystemAudioSupport {
    if !cfg!(target_os = "macos") {
        return SystemAudioSupport { supported: false, permission: "unknown".into(), message: Some("System audio capture is only available on macOS 14.2 or newer.".into()) };
    }
    if helper_path().is_none() {
        return SystemAudioSupport { supported: false, permission: "unknown".into(), message: Some("The system audio helper is missing from this build.".into()) };
    }
    SystemAudioSupport { supported: true, permission: "unknown".into(), message: None }
}

/// Creating a tap is what makes macOS ask (once, while undetermined); harmless afterwards.
pub fn request_permission() -> SystemAudioSupport {
    if let Some(helper) = helper_path() {
        let _ = Command::new(helper).arg("request").output();
    }
    support()
}

#[tauri::command]
pub fn system_audio_support() -> SystemAudioSupport {
    support()
}

#[tauri::command]
pub async fn request_system_audio_permission(app: tauri::AppHandle) -> SystemAudioSupport {
    if crate::recording::is_recording(&app) {
        return support();
    }
    tauri::async_runtime::spawn_blocking(request_permission).await.unwrap_or_else(|_| support())
}

pub struct SystemTap {
    child: Child,
    pub level: Arc<Mutex<f32>>,
}

impl SystemTap {
    /// Start capturing to `out_wav`. Blocks until the helper reports READY (or fails).
    pub fn start(out_wav: &Path) -> Result<SystemTap, String> {
        let helper = helper_path().ok_or("System audio helper is missing from this build.")?;
        let mut child = Command::new(&helper)
            .arg("record")
            .arg(out_wav)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("Could not start system audio capture: {e}"))?;
        let stdout = child.stdout.take().ok_or("no stdout")?;
        let level = Arc::new(Mutex::new(0f32));
        let (tx, rx) = std::sync::mpsc::channel::<Result<(), String>>();
        let lvl = level.clone();
        std::thread::spawn(move || {
            let mut ready = false;
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if line == "READY" {
                    ready = true;
                    let _ = tx.send(Ok(()));
                } else if let Some(v) = line.strip_prefix("level ") {
                    if let Ok(f) = v.trim().parse::<f32>() {
                        if let Ok(mut g) = lvl.lock() {
                            *g = f;
                        }
                    }
                }
            }
            if !ready {
                let _ = tx.send(Err("helper exited before capture started".into()));
            }
        });
        match rx.recv_timeout(Duration::from_secs(15)) {
            Ok(Ok(())) => Ok(SystemTap { child, level }),
            Ok(Err(_)) | Err(_) => {
                let _ = child.kill();
                let mut err = String::new();
                if let Some(mut e) = child.stderr.take() {
                    use std::io::Read;
                    let _ = e.read_to_string(&mut err);
                }
                let code = child.wait().ok().and_then(|s| s.code());
                if code == Some(2) || err.contains("permission-denied") {
                    Err("permission-denied".into())
                } else {
                    Err(format!("System audio capture could not start. {}", err.trim()))
                }
            }
        }
    }

    pub fn stop(mut self) -> Result<(), String> {
        if let Some(mut stdin) = self.child.stdin.take() {
            let _ = stdin.write_all(b"stop\n");
            let _ = stdin.flush();
            drop(stdin);
        }
        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            if let Ok(Some(_)) = self.child.try_wait() {
                return Ok(());
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
        Ok(())
    }
}

#[tauri::command]
pub fn open_system_audio_settings() -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_AudioCapture")
            .status()
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}

#[derive(Serialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct LocalePrefs {
    pub locale: Option<String>,   // e.g. "nl-NL"
    pub force24_hour: Option<bool>,
}

/// The OS-level locale and 12/24-hour preference, so dates and times follow the system.
#[tauri::command]
pub fn get_locale_prefs() -> LocalePrefs {
    fn defaults(key: &str) -> Option<String> {
        let o = Command::new("defaults").args(["read", "-g", key]).output().ok()?;
        if !o.status.success() {
            return None;
        }
        Some(String::from_utf8_lossy(&o.stdout).trim().to_string())
    }
    // "en_US@rg=nlzzzz" = English with the Netherlands as region → "en-NL" (dates/times follow the region).
    let locale = defaults("AppleLocale").and_then(|raw| {
        let (base, extra) = raw.split_once('@').map(|(a, b)| (a.to_string(), Some(b.to_string()))).unwrap_or((raw.clone(), None));
        let mut parts = base.split('_');
        let lang = parts.next().unwrap_or("en").to_string();
        let mut region = parts.next().map(|r| r.to_string());
        if let Some(extra) = extra {
            for kv in extra.split(';') {
                if let Some(r) = kv.strip_prefix("rg=") {
                    if r.len() >= 2 {
                        region = Some(r[..2].to_uppercase());
                    }
                }
            }
        }
        if lang.is_empty() { None } else { Some(match region { Some(r) => format!("{lang}-{r}"), None => lang }) }
    });
    let force = defaults("AppleICUForce24HourTime").map(|v| v == "1");
    let force12 = defaults("AppleICUForce12HourTime").map(|v| v == "1");
    LocalePrefs { locale, force24_hour: match (force, force12) { (Some(true), _) => Some(true), (_, Some(true)) => Some(false), _ => None } }
}

/// Two-letter language of the system locale ("nl" for "nl-NL"); the engine uses it as the
/// default notes language. Falls back to English when the locale cannot be read.
pub fn system_language() -> String {
    get_locale_prefs()
        .locale
        .and_then(|l| l.split('-').next().map(|s| s.to_lowercase()))
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| "en".to_string())
}
