//! The few preferences the shell needs when the engine is not running: whether Huddle stays
//! in the menu bar, and which microphone / system-audio choice a tray or shortcut recording
//! should use. Stored as `<data>/shell.json`; the UI keeps it in step with the engine settings.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(rename_all = "camelCase", default)]
pub struct ShellPrefs {
    pub menu_bar: bool,
    pub input_device: Option<String>,
    pub system_audio: bool,
    pub sounds: bool,
}

impl Default for ShellPrefs {
    /// A fresh install lives in the menu bar; the engine's `general.menuBar` default agrees.
    fn default() -> Self {
        Self { menu_bar: true, input_device: None, system_audio: false, sounds: true }
    }
}

fn path(app: &AppHandle) -> Option<PathBuf> {
    crate::paths::data_dir(app).ok().map(|d| d.join("shell.json"))
}

pub fn load(app: &AppHandle) -> ShellPrefs {
    path(app)
        .and_then(|p| std::fs::read_to_string(p).ok())
        .and_then(|s| serde_json::from_str(&s).ok())
        .unwrap_or_default()
}

pub fn save(app: &AppHandle, prefs: &ShellPrefs) -> Result<(), String> {
    let p = path(app).ok_or("no data dir")?;
    std::fs::write(&p, serde_json::to_vec_pretty(prefs).map_err(|e| e.to_string())?).map_err(|e| e.to_string())
}

#[tauri::command]
pub fn get_shell_prefs(app: AppHandle) -> ShellPrefs {
    load(&app)
}

#[derive(Deserialize, Default)]
#[serde(rename_all = "camelCase", default)]
pub struct ShellPrefsPatch {
    pub menu_bar: Option<bool>,
    pub input_device: Option<Option<String>>,
    pub system_audio: Option<bool>,
    pub sounds: Option<bool>,
}

/// Merge a patch, persist it, and apply what has an immediate effect (the tray icon).
#[tauri::command]
pub fn set_shell_prefs(app: AppHandle, patch: ShellPrefsPatch) -> Result<ShellPrefs, String> {
    let mut prefs = load(&app);
    if let Some(v) = patch.menu_bar {
        prefs.menu_bar = v;
    }
    if let Some(v) = patch.input_device {
        prefs.input_device = v;
    }
    if let Some(v) = patch.system_audio {
        prefs.system_audio = v;
    }
    if let Some(v) = patch.sounds {
        prefs.sounds = v;
    }
    save(&app, &prefs)?;
    // The popover can change the recording choice while the engine sleeps; the main window
    // listens and writes it back into the engine settings.
    let _ = app.emit("shell-prefs:changed", prefs.clone());
    if prefs.menu_bar {
        crate::tray::ensure(&app);
    } else {
        crate::tray::remove(&app);
        // A window that was hidden into the menu bar must come back when the mode is switched off.
        crate::tray::show_main_window(&app);
    }
    Ok(prefs)
}
