//! Platform-appropriate application directories.
//!
//! macOS: ~/Library/Application Support/com.huddle.desktop
//! Windows (future): %APPDATA%\com.huddle.desktop
//! `HUDDLE_DATA_DIR` overrides everything (used by tests and development).

use std::path::PathBuf;

use serde::Serialize;
use tauri::{AppHandle, Manager};

pub fn data_dir(app: &AppHandle) -> anyhow::Result<PathBuf> {
    if let Ok(custom) = std::env::var("HUDDLE_DATA_DIR") {
        if !custom.trim().is_empty() {
            return Ok(PathBuf::from(custom));
        }
    }
    Ok(app.path().app_data_dir()?)
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AppPaths {
    pub data_dir: String,
    pub recordings_dir: String,
    pub logs_dir: String,
}

#[tauri::command]
pub fn get_paths(app: AppHandle) -> Result<AppPaths, String> {
    let data = data_dir(&app).map_err(|e| e.to_string())?;
    let logs = app.path().app_log_dir().map_err(|e| e.to_string())?;
    Ok(AppPaths {
        recordings_dir: data.join("recordings").to_string_lossy().into_owned(),
        data_dir: data.to_string_lossy().into_owned(),
        logs_dir: logs.to_string_lossy().into_owned(),
    })
}
