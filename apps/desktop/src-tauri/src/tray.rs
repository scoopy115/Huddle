//! Menu-bar mode: a tray icon that survives closing the window, with a popover that shows the
//! recording UI (timer, level meter, start/stop) and ⌥⌘R for the same without any UI. Recording
//! itself is the shell's job (cpal + the system-audio helper), so it works while the Python
//! engine is stopped and never touches the main window or the Dock. Only stopping brings the
//! main window back (and the engine up); the UI then picks the recording up from the pending
//! queue and processes it.

use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::image::Image;

use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_positioner::{Position, WindowExt};

pub const TRAY_ID: &str = "huddle-tray";
/// Label of the popover window (declared in tauri.conf.json, hidden until the icon is clicked).
pub const POPOVER: &str = "tray";
pub const SHORTCUT_LABEL: &str = "⌥⌘R";

fn record_label(recording: bool) -> String {
    if recording { format!("Stop Recording\t{SHORTCUT_LABEL}") } else { format!("Start Recording\t{SHORTCUT_LABEL}") }
}

/// Right-click menu; the left click opens the popover.
fn build_menu(app: &AppHandle, recording: bool) -> tauri::Result<tauri::menu::Menu<tauri::Wry>> {
    MenuBuilder::new(app)
        .item(&MenuItemBuilder::with_id("tray-record", record_label(recording)).build(app)?)
        .separator()
        .item(&MenuItemBuilder::with_id("tray-open", "Open Huddle").build(app)?)
        .separator()
        .item(&MenuItemBuilder::with_id("tray-quit", "Quit Huddle").build(app)?)
        .build()
}

/// Run on the main thread. Tray handles are `Arc`s whose last drop uninstalls the status item,
/// and AppKit traps when that happens off the main thread — so every place that obtains one
/// goes through here (commands run on a worker thread in Tauri 2).
fn on_main(app: &AppHandle, f: impl FnOnce(&AppHandle) + Send + 'static) {
    let a = app.clone();
    let _ = app.run_on_main_thread(move || f(&a));
}

/// Create the tray icon if it does not exist yet.
pub fn ensure(app: &AppHandle) {
    on_main(app, ensure_now);
}

fn ensure_now(app: &AppHandle) {
    if app.tray_by_id(TRAY_ID).is_some() {
        return;
    }
    let recording = crate::recording::is_recording(app);
    let Ok(menu) = build_menu(app, recording) else { return };
    let icon = tauri::image::Image::from_bytes(include_bytes!("../icons/tray-template.png")).ok();
    let mut builder = TrayIconBuilder::with_id(TRAY_ID)
        .menu(&menu)
        .show_menu_on_left_click(false)
        .tooltip("Huddle")
        .on_menu_event(|app, event| match event.id().0.as_str() {
            "tray-record" => toggle_recording(app.clone()),
            "tray-open" => {
                hide_popover(app);
                show_main_window(app);
            }
            "tray-quit" => app.exit(0),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            let app = tray.app_handle();
            tauri_plugin_positioner::on_tray_event(app, &event);
            if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                toggle_popover(app);
            }
        });
    if let Some(icon) = icon {
        builder = builder.icon(icon).icon_as_template(true);
    }
    match builder.build(app) {
        Ok(_) => {
            log::info!("menu bar icon ready");
            set_anim(app, None, None);
        }
        Err(e) => log::error!("tray icon failed: {e}"),
    }
}

pub fn remove(app: &AppHandle) {
    hide_popover(app);
    on_main(app, |app| {
        let _ = app.remove_tray_by_id(TRAY_ID);
    });
}

/// Keep the menu item text and the icon state in step with the recorder.
pub fn refresh(app: &AppHandle, recording: bool) {
    on_main(app, move |app| {
        if let Some(tray) = app.tray_by_id(TRAY_ID) {
            if let Ok(menu) = build_menu(app, recording) {
                let _ = tray.set_menu(Some(menu));
            }
            let _ = tray.set_tooltip(Some(if recording { "Huddle — recording" } else { "Huddle" }));
        }
    });
    set_anim(app, Some(recording), None);
}

// ---- Icon states -------------------------------------------------------------------------------
// Recording: the "h" with a blinking red dot. A template image cannot carry colour, so the dot
// frames are plain images with a black or white "h", chosen by the menu bar's *actual*
// appearance: on macOS 26 the transparent bar tints its icons by the wallpaper behind it, so the
// light/dark setting is not enough — but our own status item's window reports the appearance
// AppKit really draws with. The "off" phase is a same-width template frame, so nothing shifts.
// Processing: the "h" with a spinning arc, as template frames. Recording wins when both apply.
// Frames are pre-rendered by scripts/make-tray-states.swift.

const ICON_BASE: &[u8] = include_bytes!("../icons/tray-template.png");
const ICON_REC_LIGHT: &[u8] = include_bytes!("../icons/tray/recording-light.png");
const ICON_REC_DARK: &[u8] = include_bytes!("../icons/tray/recording-dark.png");
const ICON_REC_OFF: &[u8] = include_bytes!("../icons/tray/recording-off.png");
const ICON_BUSY: [&[u8]; 8] = [
    include_bytes!("../icons/tray/busy-0.png"),
    include_bytes!("../icons/tray/busy-1.png"),
    include_bytes!("../icons/tray/busy-2.png"),
    include_bytes!("../icons/tray/busy-3.png"),
    include_bytes!("../icons/tray/busy-4.png"),
    include_bytes!("../icons/tray/busy-5.png"),
    include_bytes!("../icons/tray/busy-6.png"),
    include_bytes!("../icons/tray/busy-7.png"),
];

struct Anim {
    recording: bool,
    busy: bool,
    running: bool,
}

static ANIM: Mutex<Anim> = Mutex::new(Anim { recording: false, busy: false, running: false });
static LOGGED_APPEARANCE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// Update the state; one long-lived animation thread reacts to it. (An earlier version exited the
/// thread when idle and lost the spinner when "processing" arrived while the thread was still
/// tearing down — a state change and the exit decision were not atomic.)
fn set_anim(app: &AppHandle, recording: Option<bool>, busy: Option<bool>) {
    let spawn = {
        let mut s = ANIM.lock().unwrap();
        if let Some(r) = recording {
            s.recording = r;
        }
        if let Some(b) = busy {
            s.busy = b;
        }
        if s.running { false } else { s.running = true; true }
    };
    if spawn {
        let app = app.clone();
        std::thread::spawn(move || animate(app));
    }
}

/// Whether the menu bar draws its icons white, read from the status-item window AppKit created
/// for our icon (`NSStatusBarWindow`). Main thread only; `None` when it cannot be found.
#[cfg(target_os = "macos")]
fn menu_bar_is_dark() -> Option<bool> {
    use objc2::MainThreadMarker;
    use objc2_app_kit::{NSAppearanceCustomization, NSApplication};
    let mtm = MainThreadMarker::new()?;
    let app = NSApplication::sharedApplication(mtm);
    let windows = app.windows();
    for w in windows.iter() {
        let obj: &objc2::runtime::AnyObject = &w;
        let cls = obj.class().name().to_string_lossy().to_string();
        if cls.contains("StatusBarWindow") {
            let name = w.effectiveAppearance().name().to_string().to_lowercase();
            return Some(name.contains("dark"));
        }
    }
    None
}

#[cfg(not(target_os = "macos"))]
fn menu_bar_is_dark() -> Option<bool> {
    None
}

/// Fallback: the system appearance as reported to the window.
fn theme_is_dark(app: &AppHandle) -> bool {
    app.get_webview_window("main").and_then(|w| w.theme().ok()) == Some(tauri::Theme::Dark)
}

/// `set_icon` alone would install the new image as a non-template one (the flag belongs to the
/// image, not the tray), which rendered every template frame black; set both together.
fn show(tray: &tauri::tray::TrayIcon, bytes: &[u8], template: bool) {
    let _ = tray.set_icon_with_as_template(Image::from_bytes(bytes).ok(), template);
}

fn animate(app: AppHandle) {
    let mut frame = 0usize;
    let mut idle_shown = false;
    loop {
        let (recording, busy) = {
            let s = ANIM.lock().unwrap();
            (s.recording, s.busy)
        };
        if !recording && !busy {
            if !idle_shown {
                on_main(&app, |app| {
                    if let Some(tray) = app.tray_by_id(TRAY_ID) {
                        show(&tray, ICON_BASE, true);
                    }
                });
                idle_shown = true;
                frame = 0;
            }
            std::thread::sleep(Duration::from_millis(250));
            continue;
        }
        idle_shown = false;
        if recording {
            let on = frame % 2 == 0;
            on_main(&app, move |app| {
                let Some(tray) = app.tray_by_id(TRAY_ID) else { return };
                if on {
                    let dark = menu_bar_is_dark().unwrap_or_else(|| theme_is_dark(app));
                    if !LOGGED_APPEARANCE.swap(true, std::sync::atomic::Ordering::Relaxed) {
                        log::info!("menu bar appearance: {}", if dark { "dark" } else { "light" });
                    }
                    show(&tray, if dark { ICON_REC_DARK } else { ICON_REC_LIGHT }, false);
                } else {
                    show(&tray, ICON_REC_OFF, true);
                }
            });
            std::thread::sleep(Duration::from_millis(650));
        } else {
            let bytes = ICON_BUSY[frame % ICON_BUSY.len()];
            on_main(&app, move |app| {
                if let Some(tray) = app.tray_by_id(TRAY_ID) {
                    show(&tray, bytes, true);
                }
            });
            std::thread::sleep(Duration::from_millis(110));
        }
        frame += 1;
    }
}

/// The UI reports whether any meeting is processing.
#[tauri::command]
pub fn tray_set_busy(app: AppHandle, busy: bool) {
    set_anim(&app, None, Some(busy));
}

/// When the popover last closed because it lost focus. Clicking the icon while it is open
/// blurs (and hides) it first, so the click must not open it again.
static LAST_BLUR: Mutex<Option<Instant>> = Mutex::new(None);

pub fn popover_blurred(window: &tauri::Window) {
    let _ = window.hide();
    *LAST_BLUR.lock().unwrap() = Some(Instant::now());
}

/// Show the popover under the icon, or hide it when it is already open.
pub fn toggle_popover(app: &AppHandle) {
    let Some(w) = app.get_webview_window(POPOVER) else { return };
    let just_blurred = LAST_BLUR.lock().unwrap().is_some_and(|t| t.elapsed() < Duration::from_millis(400));
    if w.is_visible().unwrap_or(false) {
        let _ = w.hide();
    } else if just_blurred {
        // The click that closed it; leave it closed.
    } else {
        let _ = w.move_window(Position::TrayCenter);
        let _ = w.show();
        let _ = w.set_focus();
        let _ = w.emit("tray:shown", ());
    }
}

pub fn hide_popover(app: &AppHandle) {
    if let Some(w) = app.get_webview_window(POPOVER) {
        let _ = w.hide();
    }
}

/// Bring the main window back (also from the hidden, Dock-less menu-bar state).
pub fn show_main_window(app: &AppHandle) {
    log::info!("showing main window (Dock icon on)");
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Regular);
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
    crate::engine::ensure_running(app);
}

/// Hide into the menu bar: no window, no Dock icon; the engine is stopped when idle.
pub fn hide_to_menu_bar(app: &AppHandle) {
    log::info!("hiding to menu bar (Dock icon off)");
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.hide();
    }
    #[cfg(target_os = "macos")]
    let _ = app.set_activation_policy(tauri::ActivationPolicy::Accessory);
    crate::engine::stop_if_idle(app);
}

/// ⌥⌘R, the popover button and the menu item: start a recording with the shell's saved
/// preferences (nothing else moves — no window, no Dock icon), or stop the running one, hand it
/// to the UI for processing and open Huddle.
pub fn toggle_recording(app: AppHandle) {
    std::thread::spawn(move || {
        if crate::recording::is_recording(&app) {
            match crate::recording::stop(&app) {
                Ok(meta) => {
                    crate::sounds::play(&app, crate::sounds::Chime::RecordStop);
                    crate::recording::push_pending(&app, meta.clone());
                    hide_popover(&app);
                    show_main_window(&app);
                    let _ = app.emit("recording:stopped", meta);
                }
                Err(e) => {
                    log::error!("stop recording from tray failed: {e}");
                    let _ = app.emit("recording:error", e);
                }
            }
        } else {
            let prefs = crate::shell_prefs::load(&app);
            let mut result = crate::recording::start(&app, prefs.input_device.clone(), prefs.system_audio);
            if prefs.system_audio && result.as_ref().is_err_and(|e| e == "permission-denied") {
                // Screen & System Audio Recording is not (or no longer) granted. Record the
                // microphone anyway rather than failing silently from a shortcut.
                log::warn!("system audio not permitted; recording microphone only");
                result = crate::recording::start(&app, prefs.input_device.clone(), false);
                if result.is_ok() {
                    let _ = app.emit("recording:warning", "System audio is off: macOS has not allowed Huddle to record other apps. Allow it under Privacy & Security in System Settings.");
                }
            }
            match result {
                Ok(meta) => {
                    crate::sounds::play(&app, crate::sounds::Chime::RecordStart);
                    let _ = app.emit("recording:started", meta);
                }
                Err(e) => {
                    log::error!("start recording from tray failed: {e}");
                    let _ = app.emit("recording:error", e);
                }
            }
        }
    });
}

#[tauri::command]
pub fn tray_toggle_recording(app: AppHandle) {
    toggle_recording(app);
}

#[tauri::command]
pub fn tray_open_main(app: AppHandle) {
    hide_popover(&app);
    show_main_window(&app);
}

#[tauri::command]
pub fn tray_hide(app: AppHandle) {
    hide_popover(&app);
}

#[tauri::command]
pub fn tray_quit(app: AppHandle) {
    app.exit(0);
}
