//! Update check and download. The check asks the GitHub API for the latest release of the
//! Huddle repository and compares its tag with the running version. "Download" fetches the
//! release's `.dmg` into `~/Downloads/` and opens it: Finder mounts the image and shows the
//! drag-to-Applications window (scripts/make-dmg.sh), so updating is the same gesture as the
//! first install. (Replacing the running bundle in place was tried and dropped: translocated or
//! read-only locations made it fail in ways users could not act on.) A missing repository or
//! release (404) simply means "no update" so the check is safe before the first release exists.

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

/// The macOS disk image among the release assets: a `.dmg` whose name hints at macOS/Apple
/// Silicon, otherwise the only/first `.dmg`. (Releases also carry a `.zip` for the updaters of
/// 0.5.2–0.6.1; this version ignores it.)
fn pick_asset(assets: &[Asset]) -> Option<&Asset> {
    let zips: Vec<&Asset> = assets.iter().filter(|a| a.name.to_lowercase().ends_with(".dmg")).collect();
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
    /// The downloaded disk image (`~/Downloads/Huddle-<version>-macos-arm64.dmg`), already opened.
    pub dmg_path: String,
}

fn emit(app: &AppHandle, phase: &str, downloaded: u64, total: Option<u64>) {
    let _ = app.emit("update:progress", UpdateProgress { phase: phase.into(), downloaded, total });
}

/// The `.app` bundle the running executable lives in, if any (none in `tauri dev`).
fn running_bundle() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?.canonicalize().ok()?;
    exe.ancestors().find(|p| p.extension().is_some_and(|e| e == "app")).map(Path::to_path_buf)
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
pub async fn install_update(app: AppHandle, asset_url: String, asset_name: Option<String>, version: String) -> Result<InstallOutcome, String> {
    if !cfg!(target_os = "macos") {
        return Err("Automatic updates are only available on macOS.".into());
    }
    // Straight into Downloads, named after the version so several versions never collide.
    let downloads = app.path().download_dir().unwrap_or_else(|_| std::env::var("HOME").map(|h| PathBuf::from(h).join("Downloads")).unwrap_or_else(|_| PathBuf::from("/tmp")));
    std::fs::create_dir_all(&downloads).map_err(|e| format!("Could not create {}: {e}", downloads.display()))?;
    let name = asset_name.filter(|n| n.to_lowercase().ends_with(".dmg")).unwrap_or_else(|| format!("Huddle-{}-macos-arm64.dmg", version.trim_start_matches('v')));
    let dmg = downloads.join(&name);
    let part = downloads.join(format!("{name}.part"));

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
    let mut file = tokio::fs::File::create(&part).await.map_err(|e| e.to_string())?;
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
    let _ = std::fs::remove_file(&dmg);
    std::fs::rename(&part, &dmg).map_err(|e| format!("Could not save the download: {e}"))?;

    // 2. Open it: Finder mounts the image and shows the drag-to-Applications window. The image is
    //    signed and notarized, so no Gatekeeper prompt stands in the way.
    emit(&app, "opening", downloaded, total);
    run("open", &[dmg.as_os_str()]).await?;
    Ok(InstallOutcome { dmg_path: dmg.display().to_string() })
}

/// Re-open a downloaded disk image (the dialog's "Open again" after the user closed the window).
#[tauri::command]
pub async fn open_download(path: String) -> Result<(), String> {
    if !path.to_lowercase().ends_with(".dmg") {
        return Err("Not a disk image.".into());
    }
    run("open", &[std::ffi::OsStr::new(&path)]).await
}
