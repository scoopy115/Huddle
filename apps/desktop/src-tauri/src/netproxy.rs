//! LAN forwarder for the network MCP server.
//!
//! The Python engine listens for MCP over HTTP on loopback only. When "Network access" is on,
//! the shell opens `0.0.0.0:<mcp.port>` itself and forwards every connection to the engine.
//! Consequences: macOS attributes the "accept incoming network connections?" firewall decision
//! to Huddle (the app the user recognises) instead of to an unsigned Python binary, the prompt
//! appears the moment the switch is flipped, and the engine process is never exposed directly.

use std::sync::Mutex;

use serde::Serialize;
use tokio::net::{TcpListener, TcpStream};

#[derive(Serialize, Clone, Debug, Default)]
#[serde(rename_all = "camelCase")]
pub struct ProxyStatus {
    pub running: bool,
    pub port: Option<u16>,
    pub target_port: Option<u16>,
    pub error: Option<String>,
}

struct Running {
    port: u16,
    target_port: u16,
    task: tauri::async_runtime::JoinHandle<()>,
}

#[derive(Default)]
pub struct ProxyState {
    inner: Mutex<Option<Running>>,
    last_error: Mutex<Option<String>>,
}

fn status_of(state: &ProxyState) -> ProxyStatus {
    let g = state.inner.lock().unwrap();
    let err = state.last_error.lock().unwrap().clone();
    match g.as_ref() {
        Some(r) => ProxyStatus { running: true, port: Some(r.port), target_port: Some(r.target_port), error: None },
        None => ProxyStatus { running: false, port: None, target_port: None, error: err },
    }
}

fn stop_inner(state: &ProxyState) {
    if let Some(r) = state.inner.lock().unwrap().take() {
        r.task.abort();
        log::info!("network forwarder on port {} stopped", r.port);
    }
}

/// Start (or re-point) the forwarder: public `port` on all interfaces → `127.0.0.1:target_port`.
#[tauri::command]
pub async fn network_proxy_start(state: tauri::State<'_, ProxyState>, port: u16, target_port: u16) -> Result<ProxyStatus, String> {
    // Decide with the guard dropped: `status_of` takes the same (non-reentrant) lock.
    let already = matches!(state.inner.lock().unwrap().as_ref(), Some(r) if r.port == port && r.target_port == target_port);
    if already {
        return Ok(status_of(&state));
    }
    stop_inner(&state);
    // Binding here (not inside the spawned task) makes the firewall prompt and any
    // "address in use" error happen right when the user flips the switch.
    let listener = match TcpListener::bind(("0.0.0.0", port)).await {
        Ok(l) => l,
        Err(e) => {
            let msg = if e.kind() == std::io::ErrorKind::AddrInUse {
                format!("Port {port} is already in use on this Mac. Choose another port.")
            } else {
                format!("Port {port} could not be opened: {e}")
            };
            *state.last_error.lock().unwrap() = Some(msg.clone());
            return Err(msg);
        }
    };
    let task = tauri::async_runtime::spawn(async move {
        loop {
            let Ok((mut inbound, peer)) = listener.accept().await else { continue };
            tauri::async_runtime::spawn(async move {
                match TcpStream::connect(("127.0.0.1", target_port)).await {
                    Ok(mut outbound) => {
                        let _ = tokio::io::copy_bidirectional(&mut inbound, &mut outbound).await;
                    }
                    Err(e) => log::warn!("network forwarder: engine unreachable for {peer}: {e}"),
                }
            });
        }
    });
    *state.inner.lock().unwrap() = Some(Running { port, target_port, task });
    *state.last_error.lock().unwrap() = None;
    log::info!("network forwarder: 0.0.0.0:{port} → 127.0.0.1:{target_port}");
    Ok(status_of(&state))
}

/// Async so a lock is never awaited on the main thread.
#[tauri::command]
pub async fn network_proxy_stop(state: tauri::State<'_, ProxyState>) -> Result<ProxyStatus, String> {
    stop_inner(&state);
    *state.last_error.lock().unwrap() = None;
    Ok(status_of(&state))
}

#[tauri::command]
pub async fn network_proxy_status(state: tauri::State<'_, ProxyState>) -> Result<ProxyStatus, String> {
    Ok(status_of(&state))
}

/// System Settings → Network → Firewall, where the user can allow Huddle if they dismissed the prompt.
#[tauri::command]
pub fn open_firewall_settings() -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg("x-apple.systempreferences:com.apple.Network-Settings.extension?Firewall")
            .status()
            .map_err(|e| e.to_string())?;
    }
    Ok(())
}
