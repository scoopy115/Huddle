//! Small file helpers for the UI. The Tauri fs/opener plugins' scoped permissions are not used
//! for these two cases: the only file the UI ever writes is an export to a path the user just
//! picked in the save dialog, and "Show in Finder" only ever targets Huddle's own folders.

#[tauri::command]
pub fn save_text_file(path: String, contents: String) -> Result<(), String> {
    std::fs::write(&path, contents).map_err(|e| format!("Could not write {path}: {e}"))
}

/// Reveal a folder (or a file's folder) in Finder. Windows/Linux: open the folder.
#[tauri::command]
pub fn reveal_in_finder(path: String) -> Result<(), String> {
    let p = std::path::Path::new(&path);
    if !p.exists() {
        return Err(format!("{path} does not exist."));
    }
    #[cfg(target_os = "macos")]
    let status = std::process::Command::new("open").arg(if p.is_dir() { "" } else { "-R" }).arg(&path).status();
    #[cfg(target_os = "windows")]
    let status = std::process::Command::new("explorer").arg(&path).status();
    #[cfg(all(unix, not(target_os = "macos")))]
    let status = std::process::Command::new("xdg-open").arg(&path).status();
    match status {
        Ok(s) if s.success() => Ok(()),
        Ok(s) => Err(format!("Finder returned {s}")),
        Err(e) => Err(e.to_string()),
    }
}
