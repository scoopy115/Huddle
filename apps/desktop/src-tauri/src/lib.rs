//! Huddle desktop shell.
//!
//! Rust owns what benefits from native access: microphone capture (cpal), device
//! enumeration, hardware detection, application paths, the macOS menu bar, and the
//! lifecycle of the meeting-processing engine (a Python sidecar on localhost). Everything
//! about meetings, transcripts, models and search lives in the engine and is reached
//! through the single `engine_fetch` proxy command.

mod devices;
mod engine;
mod files;
mod hardware;
mod netproxy;
mod paths;
mod recording;
mod shell_prefs;
mod sounds;
mod system_audio;
mod tray;
mod updates;

use tauri::menu::{AboutMetadata, MenuBuilder, MenuItemBuilder, SubmenuBuilder};
use tauri::{Emitter, Manager};
use tauri_plugin_global_shortcut::{Builder as ShortcutBuilder, ShortcutState};

/// The application menu. Its accelerators are the app's keyboard shortcuts: macOS routes
/// them to menu events before the webview sees the key, so the UI only listens for `menu`.
fn build_menu(app: &tauri::App) -> tauri::Result<tauri::menu::Menu<tauri::Wry>> {
    let about = AboutMetadata { name: Some("Huddle".into()), comments: Some("Private meeting notes, processed on this Mac.".into()), ..Default::default() };
    let app_menu = SubmenuBuilder::new(app, "Huddle")
        .about(Some(about))
        .separator()
        .item(&MenuItemBuilder::with_id("check-updates", "Check for Updates…").build(app)?)
        .separator()
        .item(&MenuItemBuilder::with_id("settings", "Settings…").accelerator("CmdOrCtrl+,").build(app)?)
        .separator()
        .services()
        .separator()
        .hide()
        .hide_others()
        .show_all()
        .separator()
        // Not the predefined Quit item: that one terminates through AppKit before Tauri sees it,
        // so menu-bar mode could not keep the tray alive on ⌘Q.
        .item(&MenuItemBuilder::with_id("quit", "Quit Huddle").accelerator("CmdOrCtrl+Q").build(app)?)
        .build()?;
    let file = SubmenuBuilder::new(app, "File")
        .item(&MenuItemBuilder::with_id("new-recording", "New Recording").accelerator("CmdOrCtrl+N").build(app)?)
        .item(&MenuItemBuilder::with_id("import-audio", "Import Audio…").accelerator("CmdOrCtrl+O").build(app)?)
        .separator()
        .close_window()
        .build()?;
    let edit = SubmenuBuilder::new(app, "Edit").undo().redo().separator().cut().copy().paste().select_all().build()?;
    let view = SubmenuBuilder::new(app, "View")
        .item(&MenuItemBuilder::with_id("view-meetings", "Meetings").accelerator("CmdOrCtrl+1").build(app)?)
        .item(&MenuItemBuilder::with_id("view-ask", "Ask").accelerator("CmdOrCtrl+2").build(app)?)
        .item(&MenuItemBuilder::with_id("view-actions", "Action Items").accelerator("CmdOrCtrl+3").build(app)?)
        .item(&MenuItemBuilder::with_id("view-processes", "Processes").accelerator("CmdOrCtrl+4").build(app)?)
        .separator()
        .item(&MenuItemBuilder::with_id("view-search", "Search").accelerator("CmdOrCtrl+K").build(app)?)
        .separator()
        .fullscreen()
        .build()?;
    let window = SubmenuBuilder::new(app, "Window").minimize().maximize().build()?;
    MenuBuilder::new(app).items(&[&app_menu, &file, &edit, &view, &window]).build()
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_log::Builder::new().level(log::LevelFilter::Info).build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_positioner::init())
        // ⌥⌘R anywhere on the Mac: start a recording, or stop the running one and open Huddle.
        .plugin(
            ShortcutBuilder::new()
                .with_shortcuts(["alt+super+r"])
                .expect("valid shortcut")
                .with_handler(|app, _shortcut, event| {
                    if event.state() == ShortcutState::Pressed {
                        tray::toggle_recording(app.clone());
                    }
                })
                .build(),
        )
        .manage(recording::RecorderState::default())
        .manage(engine::EngineState::default())
        .manage(netproxy::ProxyState::default())
        .setup(|app| {
            let data_dir = paths::data_dir(app.handle())?;
            std::fs::create_dir_all(data_dir.join("recordings"))?;
            log::info!("data dir: {}", data_dir.display());
            let menu = build_menu(app)?;
            app.set_menu(menu)?;
            engine::spawn_on_startup(app.handle().clone(), data_dir);
            if shell_prefs::load(app.handle()).menu_bar {
                tray::ensure(app.handle());
            }
            Ok(())
        })
        .on_menu_event(|app, event| {
            if event.id().0 == "quit" {
                // ⌘Q in menu-bar mode hides Huddle into the menu bar; "Quit Huddle" in the tray
                // menu or popover is the only way to really quit.
                if shell_prefs::load(app).menu_bar {
                    tray::hide_to_menu_bar(app);
                } else {
                    app.exit(0);
                }
                return;
            }
            let _ = app.emit("menu", event.id().0.clone());
        })
        .on_window_event(|window, event| {
            // The menu-bar popover closes as soon as it loses focus, like a system popover.
            if window.label() == tray::POPOVER {
                if let tauri::WindowEvent::Focused(false) = event {
                    tray::popover_blurred(window);
                }
                return;
            }
            match event {
            // Menu-bar mode: closing the window hides Huddle instead of quitting it.
            tauri::WindowEvent::CloseRequested { api, .. } if shell_prefs::load(window.app_handle()).menu_bar => {
                api.prevent_close();
                tray::hide_to_menu_bar(window.app_handle());
            }
            tauri::WindowEvent::Destroyed => {
                if let Some(state) = window.app_handle().try_state::<engine::EngineState>() {
                    engine::shutdown(&state);
                }
            }
            _ => {}
            }
        })
        .invoke_handler(tauri::generate_handler![
            paths::get_paths,
            files::save_text_file,
            files::reveal_in_finder,
            hardware::detect_hardware,
            devices::list_input_devices,
            recording::start_recording,
            recording::stop_recording,
            recording::recording_status,
            recording::list_unfinished_recordings,
            recording::take_pending_recordings,
            recording::discard_unfinished_recordings,
            shell_prefs::get_shell_prefs,
            shell_prefs::set_shell_prefs,
            files::copy_file,
            updates::app_info,
            tray::tray_toggle_recording,
            tray::tray_open_main,
            tray::tray_hide,
            tray::tray_quit,
            tray::tray_set_busy,
            updates::check_for_updates,
            updates::install_update,
            engine::engine_status,
            engine::engine_fetch,
            engine::engine_restart,
            engine::engine_mcp_command,
            netproxy::network_proxy_start,
            netproxy::network_proxy_stop,
            netproxy::network_proxy_status,
            netproxy::open_firewall_settings,
            system_audio::system_audio_support,
            system_audio::request_system_audio_permission,
            system_audio::open_system_audio_settings,
            system_audio::get_locale_prefs,
            system_audio::microphone_permission,
            system_audio::request_microphone_permission,
            system_audio::open_microphone_settings,
        ])
        .build(tauri::generate_context!())
        .expect("error while building Huddle")
        .run(|app, event| match event {
            // ⌘Q (or Quit from the Dock) in menu-bar mode keeps the menu-bar recorder alive;
            // only "Quit Huddle" in the tray menu (a programmatic exit) really quits.
            tauri::RunEvent::ExitRequested { code: None, api, .. } if shell_prefs::load(app).menu_bar => {
                api.prevent_exit();
                tray::hide_to_menu_bar(app);
            }
            tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit => {
                // Finish an in-progress recording so it can be recovered, and kill the engine on
                // every exit path (dev reload, crash of the webview included).
                if recording::is_recording(app) {
                    recording::stop_for_exit(app);
                }
                if let Some(state) = app.try_state::<engine::EngineState>() {
                    engine::shutdown(&state);
                }
            }
            _ => {}
        });
}
