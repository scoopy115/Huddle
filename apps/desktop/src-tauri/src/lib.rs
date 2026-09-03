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
mod system_audio;

use tauri::menu::{AboutMetadata, MenuBuilder, MenuItemBuilder, SubmenuBuilder};
use tauri::{Emitter, Manager};

/// The application menu. Its accelerators are the app's keyboard shortcuts: macOS routes
/// them to menu events before the webview sees the key, so the UI only listens for `menu`.
fn build_menu(app: &tauri::App) -> tauri::Result<tauri::menu::Menu<tauri::Wry>> {
    let about = AboutMetadata { name: Some("Huddle".into()), comments: Some("Private meeting notes, processed on this Mac.".into()), ..Default::default() };
    let app_menu = SubmenuBuilder::new(app, "Huddle")
        .about(Some(about))
        .separator()
        .item(&MenuItemBuilder::with_id("settings", "Settings…").accelerator("CmdOrCtrl+,").build(app)?)
        .separator()
        .services()
        .separator()
        .hide()
        .hide_others()
        .show_all()
        .separator()
        .quit()
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
            Ok(())
        })
        .on_menu_event(|app, event| {
            let _ = app.emit("menu", event.id().0.clone());
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                if let Some(state) = window.app_handle().try_state::<engine::EngineState>() {
                    engine::shutdown(&state);
                }
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
        ])
        .build(tauri::generate_context!())
        .expect("error while building Huddle")
        .run(|app, event| {
            // Kill the engine on every exit path (Cmd+Q, dev reload, crash of the webview).
            if matches!(event, tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }) {
                if let Some(state) = app.try_state::<engine::EngineState>() {
                    engine::shutdown(&state);
                }
            }
        });
}
