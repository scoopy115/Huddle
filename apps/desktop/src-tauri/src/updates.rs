//! Update check and self-install. The check asks the GitHub API for the latest release of the
//! Huddle repository and compares its tag with the running version. Installing downloads the
//! release's `.zip` (which contains `Huddle.app`), unpacks it with `ditto` (keeps symlinks and
//! signatures), swaps it into the place of the running bundle, and relaunches. A missing
//! repository or release (404) simply means "no update" so the check is safe before the first
//! release exists. The download is written by Huddle itself, so it carries no quarantine flag.

use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager};

const REPO: &str = "scoopy115/huddle";

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct AppInfo {
    pub version: String,
    pub build: String,
    pub bundle_path: Option<String>,
}

#[tauri::command]
pub fn app_info(app: AppHandle) -> AppInfo {
    AppInfo {
        version: app.package_info().version.to_string(),
        build: option_env!("HUDDLE_GIT_SHA").unwrap_or("dev").to_string(),
        bundle_path: running_bundle().map(|p| p.display().to_string()),
    }
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct UpdateInfo {
    pub version: String,
    pub notes: String,
    pub page_url: String,
    pub asset_url: Option<String>,
    pub asset_name: Option<String>,
    pub asset_size: Option<u64>,
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct UpdateCheck {
    pub current_version: String,
    pub update: Option<UpdateInfo>,
}

#[derive(Deserialize)]
struct Release {
    tag_name: String,
    html_url: String,
    body: Option<String>,
    #[serde(default)]
    draft: bool,
    #[serde(default)]
    assets: Vec<Asset>,
}

#[derive(Deserialize)]
struct Asset {
    name: String,
    browser_download_url: String,
    size: u64,
}

fn parse_version(tag: &str) -> Option<semver::Version> {
    semver::Version::parse(tag.trim().trim_start_matches(['v', 'V'])).ok()
}

/// The macOS zip among the release assets: a `.zip` whose name hints at macOS/Apple Silicon,
/// otherwise the only/first `.zip`.
fn pick_asset(assets: &[Asset]) -> Option<&Asset> {
    let zips: Vec<&Asset> = assets.iter().filter(|a| a.name.to_lowercase().ends_with(".zip")).collect();
    zips.iter()
        .find(|a| {
            let n = a.name.to_lowercase();
            ["mac", "darwin", "arm64", "aarch64", "apple"].iter().any(|k| n.contains(k))
        })
        .or(zips.first())
        .copied()
}

fn client(app: &AppHandle, timeout: Duration) -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(timeout)
        .user_agent(format!("Huddle/{}", app.package_info().version))
        .build()
        .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn check_for_updates(app: AppHandle) -> Result<UpdateCheck, String> {
    let current = app.package_info().version.clone();
    let none = UpdateCheck { current_version: current.to_string(), update: None };
    let resp = client(&app, Duration::from_secs(15))?
        .get(format!("https://api.github.com/repos/{REPO}/releases/latest"))
        .header("Accept", "application/vnd.github+json")
        .send()
        .await
        .map_err(|e| format!("Could not reach GitHub: {e}"))?;
    if resp.status() == reqwest::StatusCode::NOT_FOUND {
        // No repository or no release yet.
        return Ok(none);
    }
    if !resp.status().is_success() {
        return Err(format!("GitHub answered {}", resp.status()));
    }
    let rel: Release = resp.json().await.map_err(|e| format!("Unexpected answer from GitHub: {e}"))?;
    let Some(latest) = parse_version(&rel.tag_name) else { return Ok(none) };
    if rel.draft || latest <= current {
        return Ok(none);
    }
    let asset = pick_asset(&rel.assets);
    Ok(UpdateCheck {
        current_version: current.to_string(),
        update: Some(UpdateInfo {
            version: latest.to_string(),
            notes: rel.body.unwrap_or_default(),
            page_url: rel.html_url,
            asset_url: asset.map(|a| a.browser_download_url.clone()),
            asset_name: asset.map(|a| a.name.clone()),
            asset_size: asset.map(|a| a.size),
        }),
    })
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct UpdateProgress {
    pub phase: String,
    pub downloaded: u64,
    pub total: Option<u64>,
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct InstallOutcome {
    /// True when the running bundle was replaced and Huddle is about to relaunch.
    pub installed: bool,
    /// Where the unpacked `Huddle.app` sits when it could not be installed automatically.
    pub app_path: String,
    pub reason: Option<String>,
}

fn emit(app: &AppHandle, phase: &str, downloaded: u64, total: Option<u64>) {
    let _ = app.emit("update:progress", UpdateProgress { phase: phase.into(), downloaded, total });
}

/// The `.app` bundle the running executable lives in, if any (none in `tauri dev`).
fn running_bundle() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?.canonicalize().ok()?;
    exe.ancestors().find(|p| p.extension().is_some_and(|e| e == "app")).map(Path::to_path_buf)
}

fn find_app(dir: &Path) -> Option<PathBuf> {
    let is_app = |p: &Path| p.extension().is_some_and(|e| e == "app") && p.join("Contents/MacOS").is_dir();
    let mut stack = vec![dir.to_path_buf()];
    // Breadth-first, two levels: `Huddle.app` at the top or inside one wrapper folder.
    for _ in 0..2 {
        let mut next = Vec::new();
        for d in &stack {
            let Ok(entries) = std::fs::read_dir(d) else { continue };
            for e in entries.flatten() {
                let p = e.path();
                if is_app(&p) {
                    return Some(p);
                }
                if p.is_dir() {
                    next.push(p);
                }
            }
        }
        stack = next;
    }
    None
}

async fn run(cmd: &str, args: &[&std::ffi::OsStr]) -> Result<(), String> {
    let out = tokio::process::Command::new(cmd).args(args).output().await.map_err(|e| format!("{cmd}: {e}"))?;
    if out.status.success() {
        Ok(())
    } else {
        Err(format!("{cmd} failed: {}", String::from_utf8_lossy(&out.stderr).trim()))
    }
}

#[tauri::command]
pub async fn install_update(app: AppHandle, asset_url: String) -> Result<InstallOutcome, String> {
    if !cfg!(target_os = "macos") {
        return Err("Automatic updates are only available on macOS.".into());
    }
    let dir = crate::paths::data_dir(&app).map_err(|e| e.to_string())?.join("updates");
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let zip = dir.join("update.zip");

    // 1. Download, streaming to disk with progress.
    emit(&app, "downloading", 0, None);
    let mut resp = client(&app, Duration::from_secs(60 * 30))?
        .get(&asset_url)
        .send()
        .await
        .map_err(|e| format!("Download failed: {e}"))?
        .error_for_status()
        .map_err(|e| format!("Download failed: {e}"))?;
    let total = resp.content_length();
    let mut file = tokio::fs::File::create(&zip).await.map_err(|e| e.to_string())?;
    let mut downloaded = 0u64;
    let mut last_emit = 0u64;
    while let Some(chunk) = resp.chunk().await.map_err(|e| format!("Download interrupted: {e}"))? {
        tokio::io::AsyncWriteExt::write_all(&mut file, &chunk).await.map_err(|e| e.to_string())?;
        downloaded += chunk.len() as u64;
        if downloaded - last_emit > 512 * 1024 {
            last_emit = downloaded;
            emit(&app, "downloading", downloaded, total);
        }
    }
    tokio::io::AsyncWriteExt::flush(&mut file).await.map_err(|e| e.to_string())?;
    drop(file);
    emit(&app, "downloading", downloaded, total);

    // 2. Unpack. `ditto` keeps symlinks, resource forks and the code signature intact.
    emit(&app, "extracting", downloaded, total);
    let extracted = dir.join("extracted");
    std::fs::create_dir_all(&extracted).map_err(|e| e.to_string())?;
    run("ditto", &["-x".as_ref(), "-k".as_ref(), zip.as_os_str(), extracted.as_os_str()]).await?;
    let _ = std::fs::remove_file(&zip);
    let new_app = find_app(&extracted).ok_or("The download does not contain Huddle.app.")?;
    let _ = run("xattr", &["-dr".as_ref(), "com.apple.quarantine".as_ref(), new_app.as_os_str()]).await;

    // 3. Swap it in for the running bundle.
    emit(&app, "installing", downloaded, total);
    let Some(target) = running_bundle() else {
        return Ok(InstallOutcome { installed: false, app_path: new_app.display().to_string(), reason: Some("Huddle is not running from an app bundle.".into()) });
    };
    let parent = target.parent().ok_or("Bundle has no parent folder")?;
    let old = parent.join(format!(".{}.old-{}", target.file_name().and_then(|n| n.to_str()).unwrap_or("Huddle.app"), std::process::id()));
    if let Err(e) = std::fs::rename(&target, &old) {
        return Ok(InstallOutcome { installed: false, app_path: new_app.display().to_string(), reason: Some(format!("Could not replace {}: {e}", target.display())) });
    }
    let moved = match std::fs::rename(&new_app, &target) {
        Ok(()) => Ok(()),
        // Different volume: copy instead.
        Err(_) => run("ditto", &[new_app.as_os_str(), target.as_os_str()]).await,
    };
    if let Err(e) = moved {
        let _ = std::fs::rename(&old, &target);
        return Ok(InstallOutcome { installed: false, app_path: new_app.display().to_string(), reason: Some(format!("Could not install into {}: {e}", parent.display())) });
    }

    // 4. Relaunch from a detached shell once this process has exited; it also removes the old copy.
    emit(&app, "relaunching", downloaded, total);
    std::process::Command::new("/bin/sh")
        .arg("-c")
        .arg("sleep 1.5; rm -rf \"$HUDDLE_OLD\"; open \"$HUDDLE_NEW\"")
        .env("HUDDLE_OLD", &old)
        .env("HUDDLE_NEW", &target)
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .map_err(|e| format!("Installed, but could not relaunch: {e}"))?;
    let handle = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(400));
        if let Some(state) = handle.try_state::<crate::engine::EngineState>() {
            crate::engine::shutdown(&state);
        }
        handle.exit(0);
    });
    Ok(InstallOutcome { installed: true, app_path: target.display().to_string(), reason: None })
}
