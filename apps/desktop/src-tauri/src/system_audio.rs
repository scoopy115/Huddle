//! System (desktop) audio capture, permissions and locale.
//!
//! * macOS: the `huddle-audio-tap` helper (a Core Audio process tap, macOS 14.2+) captures the
//!   system mix into `system.wav`. It needs the "System Audio Recording Only" permission, which
//!   macOS offers no query for, so the shell probes the tap once (DECISIONS #63).
//! * Windows: WASAPI loopback — cpal opens the default *output* device as an input stream and
//!   delivers whatever the PC is playing. No helper, no driver, no permission.
//! * Linux: not supported yet (PipeWire/PulseAudio monitor sources are the way; later).
//!
//! The recorder (`recording.rs`) only sees `SystemTap`: start → level/heard → pause → stop.

use std::path::Path;
use std::sync::{Arc, Mutex};

use serde::Serialize;

#[derive(Serialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct SystemAudioSupport {
    pub supported: bool,        // this OS/build can capture system audio
    pub permission: String,     // granted | denied | unknown (macOS exposes no query; Windows needs none)
    pub message: Option<String>,
}

fn answer(permission: &str) -> SystemAudioSupport {
    SystemAudioSupport { supported: true, permission: permission.into(), message: None }
}

/// Current nominal sample rate of an input device (default device when `name` is None). Only
/// macOS needs this: the built-in microphone and speakers share one clock there (DECISIONS #64).
pub fn input_nominal_rate(name: Option<&str>) -> Option<u32> {
    #[cfg(target_os = "macos")]
    {
        return mac::input_nominal_rate(name);
    }
    #[allow(unreachable_code)]
    {
        let _ = name;
        None
    }
}

#[tauri::command]
pub async fn microphone_permission() -> String {
    tauri::async_runtime::spawn_blocking(platform::mic_permission).await.unwrap_or_else(|_| "unknown".into())
}

/// Shows the system's microphone prompt where there is one (macOS, once); returns the resulting state.
#[tauri::command]
pub async fn request_microphone_permission() -> String {
    tauri::async_runtime::spawn_blocking(platform::request_mic_permission).await.unwrap_or_else(|_| "unknown".into())
}

#[tauri::command]
pub fn open_microphone_settings() -> Result<(), String> {
    platform::open_microphone_settings()
}

#[tauri::command]
pub fn open_system_audio_settings() -> Result<(), String> {
    platform::open_system_audio_settings()
}

/// Availability plus the permission as far as it can be known. Never probes while a recording
/// runs (the tap is in use).
pub fn support(app: &tauri::AppHandle) -> SystemAudioSupport {
    platform::support(app)
}

#[tauri::command]
pub async fn system_audio_support(app: tauri::AppHandle) -> SystemAudioSupport {
    let a = app.clone();
    tauri::async_runtime::spawn_blocking(move || support(&a)).await.unwrap_or_else(|_| answer("unknown"))
}

/// The same probe, forced: on macOS creating the tap is what makes the system ask (once).
#[tauri::command]
pub async fn request_system_audio_permission(app: tauri::AppHandle) -> SystemAudioSupport {
    if crate::recording::is_recording(&app) {
        return support(&app);
    }
    let a = app.clone();
    tauri::async_runtime::spawn_blocking(move || platform::request(&a)).await.unwrap_or_else(|_| answer("unknown"))
}

pub use platform::SystemTap;

// ---- macOS: the audio-tap helper -------------------------------------------------------------

#[cfg(target_os = "macos")]
mod mac {
    use super::*;
    use std::io::{BufRead, BufReader, Write};
    use std::path::PathBuf;
    use std::process::{Child, Command, Stdio};
    use std::time::{Duration, Instant};

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

    pub fn mic_permission() -> String {
        mic_state("mic-check")
    }

    pub fn request_mic_permission() -> String {
        mic_state("mic-request")
    }

    pub fn open_microphone_settings() -> Result<(), String> {
        Command::new("open")
            .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")
            .status()
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn open_system_audio_settings() -> Result<(), String> {
        Command::new("open")
            .arg("x-apple.systempreferences:com.apple.preference.security?Privacy_AudioCapture")
            .status()
            .map_err(|e| e.to_string())?;
        Ok(())
    }

    pub fn input_nominal_rate(name: Option<&str>) -> Option<u32> {
        let helper = helper_path()?;
        let mut cmd = Command::new(helper);
        cmd.arg("input-rate");
        if let Some(n) = name {
            cmd.arg(n);
        }
        let out = cmd.output().ok()?;
        String::from_utf8_lossy(&out.stdout).trim().parse::<u32>().ok().filter(|r| *r >= 8000)
    }

    fn unsupported() -> Option<SystemAudioSupport> {
        if helper_path().is_none() {
            return Some(SystemAudioSupport { supported: false, permission: "unknown".into(), message: Some("The system audio helper is missing from this build.".into()) });
        }
        None
    }

    /// Run the helper's silent probe (≈0.6 s) and remember a granted answer.
    fn probe(app: &tauri::AppHandle) -> SystemAudioSupport {
        if let Some(u) = unsupported() {
            return u;
        }
        let Some(helper) = helper_path() else { return answer("unknown") };
        match Command::new(&helper).arg("check").output() {
            Ok(o) => {
                let verdict = String::from_utf8_lossy(&o.stdout).lines().filter(|l| !l.trim().is_empty()).last().unwrap_or("").trim().to_string();
                let granted = verdict == "granted";
                log::info!("system audio probe: {verdict}{}", if o.stderr.is_empty() { String::new() } else { format!(" ({})", String::from_utf8_lossy(&o.stderr).trim()) });
                crate::shell_prefs::set_system_audio_granted(app, granted);
                answer(if granted { "granted" } else { "denied" })
            }
            Err(e) => SystemAudioSupport { supported: false, permission: "unknown".into(), message: Some(format!("Helper failed: {e}")) },
        }
    }

    pub fn support(app: &tauri::AppHandle) -> SystemAudioSupport {
        if let Some(u) = unsupported() {
            return u;
        }
        if crate::shell_prefs::load(app).system_audio_granted {
            return answer("granted");
        }
        if crate::recording::is_recording(app) {
            return answer("unknown");
        }
        probe(app)
    }

    pub fn request(app: &tauri::AppHandle) -> SystemAudioSupport {
        probe(app)
    }

    pub struct SystemTap {
        child: Child,
        pub level: Arc<Mutex<f32>>,
        /// Set once any non-silent level arrived: proof that the permission is granted.
        pub heard: Arc<std::sync::atomic::AtomicBool>,
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
            let heard = Arc::new(std::sync::atomic::AtomicBool::new(false));
            let heard_w = heard.clone();
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
                            if f > 0.0 {
                                heard_w.store(true, std::sync::atomic::Ordering::Relaxed);
                            }
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
                Ok(Ok(())) => Ok(SystemTap { child, level, heard }),
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

        /// The helper keeps capturing but stops writing while paused (so `system.wav` and the
        /// microphone file stay in step). Best effort: a helper that has gone away is noticed at stop.
        pub fn set_paused(&self, paused: bool) {
            if let Some(mut stdin) = self.child.stdin.as_ref() {
                let _ = stdin.write_all(if paused { b"pause\n" } else { b"resume\n" });
                let _ = stdin.flush();
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
}

#[cfg(target_os = "macos")]
use mac as platform;

// ---- Windows: WASAPI loopback through cpal -------------------------------------------------

#[cfg(target_os = "windows")]
mod win {
    use super::*;
    use std::sync::atomic::{AtomicBool, Ordering};

    use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};

    /// Windows has a microphone privacy switch but no query API we use; a missing input device is
    /// the only state we can see. The prompt (if any) comes from the first stream the app opens.
    pub fn mic_permission() -> String {
        if cpal::default_host().default_input_device().is_some() { "granted".into() } else { "undetermined".into() }
    }

    pub fn request_mic_permission() -> String {
        mic_permission()
    }

    fn open_settings(uri: &str) -> Result<(), String> {
        let mut cmd = std::process::Command::new("cmd");
        crate::engine::quiet(&mut cmd);
        cmd.args(["/C", "start", "", uri]).spawn().map(|_| ()).map_err(|e| e.to_string())
    }

    pub fn open_microphone_settings() -> Result<(), String> {
        open_settings("ms-settings:privacy-microphone")
    }

    pub fn open_system_audio_settings() -> Result<(), String> {
        open_settings("ms-settings:sound")
    }

    pub fn support(_app: &tauri::AppHandle) -> SystemAudioSupport {
        match cpal::default_host().default_output_device() {
            Some(_) => answer("granted"),
            None => SystemAudioSupport { supported: false, permission: "unknown".into(), message: Some("No playback device found; system audio needs one.".into()) },
        }
    }

    pub fn request(app: &tauri::AppHandle) -> SystemAudioSupport {
        support(app)
    }

    /// Loopback capture of the default playback device, written as mono PCM16 to `system.wav`
    /// by the same writer the microphone uses.
    pub struct SystemTap {
        capture: Option<crate::recording::Capture>,
        /// Our own silent render stream on the same device: WASAPI loopback only delivers
        /// packets while *something* renders, so without it every quiet stretch is simply
        /// missing from `system.wav` and everything after it lands too early in the mix.
        silence: Option<cpal::Stream>,
        pause: Arc<crate::recording::Pause>,
        stopped: Arc<AtomicBool>,
        pub level: Arc<Mutex<f32>>,
        pub heard: Arc<AtomicBool>,
    }

    // cpal::Stream is !Send; the tap only moves between Tauri commands (see recording::Capture).
    unsafe impl Send for SystemTap {}

    /// A render stream of zeros that keeps the loopback timeline continuous (see `SystemTap`).
    fn start_silence(device: &cpal::Device, config: &cpal::SupportedStreamConfig) -> Option<cpal::Stream> {
        let err = |e: cpal::StreamError| log::warn!("silent render stream error: {e}");
        let cfg: cpal::StreamConfig = config.clone().into();
        let built = match config.sample_format() {
            cpal::SampleFormat::F32 => device.build_output_stream(&cfg, |d: &mut [f32], _| d.fill(0.0), err, None),
            cpal::SampleFormat::I16 => device.build_output_stream(&cfg, |d: &mut [i16], _| d.fill(0), err, None),
            cpal::SampleFormat::U16 => device.build_output_stream(&cfg, |d: &mut [u16], _| d.fill(32768), err, None),
            cpal::SampleFormat::I32 => device.build_output_stream(&cfg, |d: &mut [i32], _| d.fill(0), err, None),
            other => {
                log::warn!("no silent render stream for sample format {other:?}; quiet stretches may be dropped from system audio");
                return None;
            }
        };
        let stream = match built {
            Ok(s) => s,
            Err(e) => {
                log::warn!("silent render stream failed: {e}; quiet stretches may be dropped from system audio");
                return None;
            }
        };
        if let Err(e) = stream.play() {
            log::warn!("silent render stream would not start: {e}; quiet stretches may be dropped from system audio");
            return None;
        }
        Some(stream)
    }

    impl SystemTap {
        pub fn start(out_wav: &Path) -> Result<SystemTap, String> {
            let device = cpal::default_host().default_output_device().ok_or("No playback device to capture system audio from.")?;
            // On WASAPI an output device opened for input is a loopback stream (cpal ≥ 0.15).
            let config = device.default_output_config().map_err(|e| format!("Could not read the playback device configuration: {e}"))?;
            let silence = start_silence(&device, &config);
            let dir = out_wav.parent().map(Path::to_path_buf).ok_or("bad path")?;
            let level = Arc::new(Mutex::new(0f32));
            let heard = Arc::new(AtomicBool::new(false));
            let stopped = Arc::new(AtomicBool::new(false));
            let pause = crate::recording::Pause::new();
            let err_slot = Arc::new(Mutex::new(None));
            let cb: Arc<dyn Fn(f32, f32) + Send + Sync> = {
                let (level, heard) = (level.clone(), heard.clone());
                Arc::new(move |rms, _peak| {
                    if rms > 0.0 {
                        heard.store(true, Ordering::Relaxed);
                    }
                    if let Ok(mut g) = level.lock() {
                        *g = rms;
                    }
                })
            };
            let capture = crate::recording::start_capture(&device, config, out_wav.to_path_buf(), dir, None, stopped.clone(), pause.clone(), err_slot, cb)
                .map_err(|e| format!("System audio capture could not start. {e}"))?;
            log::info!("system audio: WASAPI loopback of {}{}", device.name().unwrap_or_default(), if silence.is_some() { " (kept fed with silence)" } else { "" });
            Ok(SystemTap { capture: Some(capture), silence, pause, stopped, level, heard })
        }

        pub fn set_paused(&self, paused: bool) {
            if paused { self.pause.pause() } else { self.pause.resume() }
        }

        pub fn stop(mut self) -> Result<(), String> {
            self.stopped.store(true, Ordering::Relaxed);
            let result = match self.capture.take() {
                Some(c) => crate::recording::finish_capture(c).map(|_| ()),
                None => Ok(()),
            };
            drop(self.silence.take());
            result
        }
    }
}

#[cfg(target_os = "windows")]
use win as platform;

// ---- Linux: microphone only for now ----------------------------------------------------------

#[cfg(all(unix, not(target_os = "macos")))]
mod other {
    use super::*;

    pub fn mic_permission() -> String {
        "granted".into()
    }

    pub fn request_mic_permission() -> String {
        "granted".into()
    }

    pub fn open_microphone_settings() -> Result<(), String> {
        Ok(())
    }

    pub fn open_system_audio_settings() -> Result<(), String> {
        Ok(())
    }

    pub fn support(_app: &tauri::AppHandle) -> SystemAudioSupport {
        SystemAudioSupport { supported: false, permission: "unknown".into(), message: Some("System audio capture is not available on Linux yet.".into()) }
    }

    pub fn request(app: &tauri::AppHandle) -> SystemAudioSupport {
        support(app)
    }

    pub struct SystemTap {
        pub level: Arc<Mutex<f32>>,
        pub heard: Arc<std::sync::atomic::AtomicBool>,
    }

    impl SystemTap {
        pub fn start(_out_wav: &Path) -> Result<SystemTap, String> {
            Err("System audio capture is not available on Linux yet.".into())
        }
        pub fn set_paused(&self, _paused: bool) {}
        pub fn stop(self) -> Result<(), String> {
            Ok(())
        }
    }
}

#[cfg(all(unix, not(target_os = "macos")))]
use other as platform;

// ---- Locale ----------------------------------------------------------------------------------

#[derive(Serialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct LocalePrefs {
    pub locale: Option<String>,   // e.g. "nl-NL"
    pub force24_hour: Option<bool>,
}

/// The OS-level locale and 12/24-hour preference, so dates and times follow the system.
#[tauri::command]
pub fn get_locale_prefs() -> LocalePrefs {
    #[cfg(target_os = "macos")]
    {
        fn defaults(key: &str) -> Option<String> {
            let o = std::process::Command::new("defaults").args(["read", "-g", key]).output().ok()?;
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
    #[cfg(target_os = "windows")]
    {
        use windows_sys::Win32::Globalization::GetUserDefaultLocaleName;
        let mut buf = [0u16; 85]; // LOCALE_NAME_MAX_LENGTH
        // SAFETY: the buffer is LOCALE_NAME_MAX_LENGTH wide, as the API requires.
        let n = unsafe { GetUserDefaultLocaleName(buf.as_mut_ptr(), buf.len() as i32) };
        let locale = (n > 1).then(|| String::from_utf16_lossy(&buf[..(n as usize - 1)])).filter(|s| !s.is_empty());
        // The 12/24-hour choice is left to the locale (Windows' own override is rarely set).
        LocalePrefs { locale, force24_hour: None }
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let raw = std::env::var("LC_TIME").or_else(|_| std::env::var("LC_ALL")).or_else(|_| std::env::var("LANG")).unwrap_or_default();
        let locale = raw.split('.').next().map(|s| s.replace('_', "-")).filter(|s| !s.is_empty() && s != "C" && s != "POSIX");
        LocalePrefs { locale, force24_hour: None }
    }
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
