//! Lifecycle of the meeting-processing engine (Python sidecar on localhost).
//!
//! * Development: `<repo>/engine/.venv/bin/python -m huddle_engine serve`.
//! * Production: the PyInstaller-built `huddle-engine` binary next to the app
//!   executable (Tauri `externalBin`) or in the resources dir.
//!
//! The engine binds 127.0.0.1 on a free port chosen here and requires a bearer
//! token generated per launch, so no other local process can read meeting data
//! through it. The frontend never talks to the engine directly; it calls
//! `engine_fetch`, which adds the token.

use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use rand::{distributions::Alphanumeric, Rng};
use serde::Serialize;
use serde_json::Value;
use tauri::{AppHandle, Emitter, Manager, State};

#[derive(Serialize, Clone, Debug, Default)]
#[serde(rename_all = "camelCase")]
pub struct EngineStatus {
    pub state: String, // starting | ready | failed | stopped
    pub port: Option<u16>,
    pub message: Option<String>,
    pub command: Option<String>,
    pub log_path: Option<String>,
}

pub struct EngineInner {
    child: Option<Child>,
    port: u16,
    token: String,
    status: EngineStatus,
    data_dir: PathBuf,
}

#[derive(Default)]
pub struct EngineState {
    inner: Mutex<Option<EngineInner>>,
}

fn free_port() -> u16 {
    std::net::TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|a| a.port())
        .unwrap_or(48731)
}

fn token() -> String {
    rand::thread_rng().sample_iter(&Alphanumeric).take(40).map(char::from).collect()
}

/// Locate how to launch the engine. Returns (program, args, description).
fn locate_engine(app: &AppHandle) -> Option<(PathBuf, Vec<String>, String)> {
    // 1. Explicit override (useful for development and CI).
    if let Ok(cmd) = std::env::var("HUDDLE_ENGINE_CMD") {
        let mut parts = cmd.split_whitespace().map(String::from).collect::<Vec<_>>();
        if !parts.is_empty() {
            let prog = PathBuf::from(parts.remove(0));
            return Some((prog, parts, format!("HUDDLE_ENGINE_CMD={cmd}")));
        }
    }
    // 2. Development builds always run the repo venv: engine code stays hot-editable, and
    //    Tauri copies `bundle.resources` next to the debug executable too, so a stale packaged
    //    sidecar must never shadow it. Release builds skip this and use the bundled sidecar.
    if cfg!(debug_assertions) {
        if let Some(dev) = dev_venv_engine() {
            return Some(dev);
        }
    }
    // 3. Bundled sidecar (release builds).
    if let Ok(resource_dir) = app.path().resource_dir() {
        for candidate in [
            resource_dir.join("engine").join("huddle-engine"),
            resource_dir.join("huddle-engine"),
        ] {
            if candidate.exists() {
                return Some((candidate.clone(), vec!["serve".into()], candidate.display().to_string()));
            }
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let sidecar = dir.join("huddle-engine");
            if sidecar.exists() {
                return Some((sidecar.clone(), vec!["serve".into()], sidecar.display().to_string()));
            }
        }
    }
    // 4. Last resort for release builds on a developer machine: the repo venv.
    dev_venv_engine()
}

/// The repository's engine venv (`python -m huddle_engine serve`), if this checkout has one.
fn dev_venv_engine() -> Option<(PathBuf, Vec<String>, String)> {
    let repo_engine = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../engine");
    let py = repo_engine.join(".venv/bin/python");
    if py.exists() {
        return Some((
            py.clone(),
            vec!["-m".into(), "huddle_engine".into(), "serve".into()],
            format!("{} -m huddle_engine serve (cwd {})", py.display(), repo_engine.display()),
        ));
    }
    None
}

#[derive(Serialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct McpCommand {
    pub program: String,
    pub args: Vec<String>,
    /// True for the development venv (python -m …); false for the packaged sidecar binary.
    pub development: bool,
}

/// The exact command an MCP client on this Mac should run to start Huddle's stdio MCP
/// server: the same program the shell uses for the engine, with `mcp --data-dir …` instead
/// of `serve`. Nothing is ever on `$PATH` — not the venv python, not the bundled sidecar —
/// so the UI shows absolute paths.
#[tauri::command]
pub fn engine_mcp_command(app: AppHandle) -> Result<McpCommand, String> {
    let (prog, mut args, _) = locate_engine(&app).ok_or_else(|| "The processing engine was not found.".to_string())?;
    if args.last().map(|a| a == "serve").unwrap_or(false) {
        args.pop();
    }
    args.push("mcp".into());
    let data_dir = crate::paths::data_dir(&app).map_err(|e| e.to_string())?;
    args.push("--data-dir".into());
    args.push(data_dir.display().to_string());
    let development = args.iter().any(|a| a == "huddle_engine");
    Ok(McpCommand { program: prog.display().to_string(), args, development })
}

fn set_status(state: &EngineState, f: impl FnOnce(&mut EngineStatus)) -> EngineStatus {
    let mut g = state.inner.lock().unwrap();
    if let Some(inner) = g.as_mut() {
        f(&mut inner.status);
        inner.status.clone()
    } else {
        EngineStatus { state: "stopped".into(), ..Default::default() }
    }
}

pub fn spawn_on_startup(app: AppHandle, data_dir: PathBuf) {
    tauri::async_runtime::spawn(async move {
        if let Err(e) = spawn(&app, data_dir).await {
            log::error!("engine failed to start: {e}");
        }
    });
}

async fn spawn(app: &AppHandle, data_dir: PathBuf) -> anyhow::Result<()> {
    let state = app.state::<EngineState>();
    let port = free_port();
    let tok = token();
    let log_dir = app.path().app_log_dir().unwrap_or_else(|_| data_dir.join("logs"));
    std::fs::create_dir_all(&log_dir)?;
    let log_path = log_dir.join("engine.log");

    let Some((prog, args, desc)) = locate_engine(app) else {
        let mut g = state.inner.lock().unwrap();
        *g = Some(EngineInner {
            child: None,
            port,
            token: tok,
            data_dir: data_dir.clone(),
            status: EngineStatus {
                state: "failed".into(),
                port: None,
                message: Some("The processing engine was not found. In development, create engine/.venv (see README).".into()),
                command: None,
                log_path: Some(log_path.display().to_string()),
            },
        });
        let _ = app.emit("engine:status", g.as_ref().unwrap().status.clone());
        anyhow::bail!("engine not found");
    };

    kill_stale_engine(&data_dir);
    log::info!("starting engine: {desc} on port {port}");
    let log_file = std::fs::OpenOptions::new().create(true).append(true).open(&log_path)?;
    let log_file_err = log_file.try_clone()?;

    let mut cmd = Command::new(&prog);
    cmd.args(&args)
        .env("HUDDLE_DATA_DIR", &data_dir)
        .env("HUDDLE_PORT", port.to_string())
        .env("HUDDLE_TOKEN", &tok)
        .env("HUDDLE_PARENT_PID", std::process::id().to_string())
        .env("HUDDLE_SYSTEM_LANGUAGE", crate::system_audio::system_language())
        // models shipped inside the app bundle (speaker separation); the engine uses them in place
        .env("HUDDLE_BUNDLED_MODELS", app.path().resource_dir().map(|d| d.join("models")).map(|d| d.display().to_string()).unwrap_or_default())
        .env("PYTHONUNBUFFERED", "1")
        .stdout(Stdio::from(log_file))
        .stderr(Stdio::from(log_file_err))
        .stdin(Stdio::null());
    if let Some(parent) = prog.parent().and_then(|p| p.parent()).and_then(|p| p.parent()) {
        // dev venv: <engine>/.venv/bin/python → cwd = <engine>
        if parent.join("huddle_engine").exists() {
            cmd.current_dir(parent);
        }
    }
    let child = cmd.spawn()?;
    let _ = std::fs::write(data_dir.join("engine.pid"), child.id().to_string());

    {
        let mut g = state.inner.lock().unwrap();
        *g = Some(EngineInner {
            child: Some(child),
            port,
            token: tok.clone(),
            data_dir: data_dir.clone(),
            status: EngineStatus {
                state: "starting".into(),
                port: Some(port),
                message: None,
                command: Some(desc.clone()),
                log_path: Some(log_path.display().to_string()),
            },
        });
        let _ = app.emit("engine:status", g.as_ref().unwrap().status.clone());
    }

    // Poll /health until ready (imports of torch etc. can take a while on first run).
    let client = reqwest::Client::builder().timeout(Duration::from_secs(3)).build()?;
    let url = format!("http://127.0.0.1:{port}/health");
    let deadline = Instant::now() + Duration::from_secs(120);
    loop {
        if Instant::now() > deadline {
            let st = set_status(&state, |s| {
                s.state = "failed".into();
                s.message = Some("The processing engine did not respond in time. See the engine log under Advanced → Diagnostics.".into());
            });
            let _ = app.emit("engine:status", st);
            anyhow::bail!("engine health timeout");
        }
        // Exited early?
        {
            let mut g = state.inner.lock().unwrap();
            if let Some(inner) = g.as_mut() {
                if let Some(child) = inner.child.as_mut() {
                    if let Ok(Some(code)) = child.try_wait() {
                        inner.status.state = "failed".into();
                        inner.status.message = Some(format!(
                            "The processing engine stopped unexpectedly (exit code {}). See the engine log under Advanced → Diagnostics.",
                            code.code().unwrap_or(-1)
                        ));
                        let _ = app.emit("engine:status", inner.status.clone());
                        anyhow::bail!("engine exited early");
                    }
                }
            }
        }
        match client.get(&url).bearer_auth(&tok).send().await {
            Ok(r) if r.status().is_success() => break,
            _ => tokio::time::sleep(Duration::from_millis(300)).await,
        }
    }
    let st = set_status(&state, |s| {
        s.state = "ready".into();
        s.message = None;
    });
    let _ = app.emit("engine:status", st);
    log::info!("engine ready on port {port}");
    Ok(())
}

pub fn shutdown(state: &EngineState) {
    if let Ok(mut g) = state.inner.lock() {
        if let Some(inner) = g.as_mut() {
            if let Some(child) = inner.child.as_mut() {
                let _ = child.kill();
                let _ = child.wait();
            }
            let _ = std::fs::remove_file(inner.data_dir.join("engine.pid"));
            inner.status.state = "stopped".into();
        }
    }
}

/// An engine left behind by a previous app instance (crash, dev reload) would keep
/// processing and confuse the UI, which only talks to the new one. Kill it.
fn kill_stale_engine(data_dir: &std::path::Path) {
    let pid_file = data_dir.join("engine.pid");
    let Ok(raw) = std::fs::read_to_string(&pid_file) else { return };
    let Ok(pid) = raw.trim().parse::<u32>() else { return };
    #[cfg(unix)]
    {
        // Only kill if that pid is really a huddle engine (pids get reused).
        let out = std::process::Command::new("ps").args(["-o", "command=", "-p", &pid.to_string()]).output();
        if let Ok(o) = out {
            let cmd = String::from_utf8_lossy(&o.stdout);
            if cmd.contains("huddle_engine") || cmd.contains("huddle-engine") {
                log::warn!("killing stale engine pid {pid}");
                let _ = std::process::Command::new("kill").args(["-9", &pid.to_string()]).status();
            }
        }
    }
    #[cfg(windows)]
    {
        let _ = std::process::Command::new("taskkill").args(["/PID", &pid.to_string(), "/F"]).status();
    }
    let _ = std::fs::remove_file(pid_file);
}

#[tauri::command]
pub fn engine_status(state: State<'_, EngineState>) -> EngineStatus {
    let g = state.inner.lock().unwrap();
    g.as_ref()
        .map(|i| i.status.clone())
        .unwrap_or(EngineStatus { state: "starting".into(), ..Default::default() })
}

#[tauri::command]
pub async fn engine_restart(app: AppHandle, state: State<'_, EngineState>) -> Result<EngineStatus, String> {
    let data_dir = {
        let g = state.inner.lock().unwrap();
        g.as_ref().map(|i| i.data_dir.clone())
    }
    .unwrap_or(crate::paths::data_dir(&app).map_err(|e| e.to_string())?);
    shutdown(&state);
    spawn(&app, data_dir).await.map_err(|e| e.to_string())?;
    Ok(engine_status(state))
}

/// Proxy an HTTP request to the engine. `path` starts with `/`.
#[tauri::command]
pub async fn engine_fetch(
    state: State<'_, EngineState>,
    method: String,
    path: String,
    body: Option<Value>,
) -> Result<Value, String> {
    let (port, tok) = {
        let g = state.inner.lock().unwrap();
        match g.as_ref() {
            Some(i) if i.status.state == "ready" => (i.port, i.token.clone()),
            Some(i) => return Err(format!("engine:{}", i.status.state)),
            None => return Err("engine:starting".into()),
        }
    };
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(600))
        .build()
        .map_err(|e| e.to_string())?;
    let url = format!("http://127.0.0.1:{port}{path}");
    let m = reqwest::Method::from_bytes(method.to_uppercase().as_bytes()).map_err(|e| e.to_string())?;
    let mut req = client.request(m, &url).bearer_auth(&tok);
    if let Some(b) = body {
        req = req.json(&b);
    }
    let resp = req.send().await.map_err(|e| format!("engine unreachable: {e}"))?;
    let status = resp.status();
    let text = resp.text().await.map_err(|e| e.to_string())?;
    let value: Value = if text.is_empty() {
        Value::Null
    } else {
        serde_json::from_str(&text).unwrap_or(Value::String(text.clone()))
    };
    if status.is_success() {
        Ok(value)
    } else {
        let detail = value
            .get("detail")
            .and_then(|d| d.as_str().map(String::from).or_else(|| Some(d.to_string())))
            .unwrap_or_else(|| text.clone());
        Err(format!("{}: {}", status.as_u16(), detail))
    }
}
