//! Reliable microphone (+ optional system audio) recording.
//!
//! Design goals (spec §10): never hold a long recording in memory, leave a
//! playable file after a crash, and store metadata while recording.
//!
//! * cpal input stream on the chosen device, downmixed to mono, converted to PCM16.
//! * A dedicated writer thread appends samples to `audio.wav` and **rewrites the
//!   RIFF/data length fields every second**, so the header is valid even if the
//!   process dies. `recording.json` is updated alongside with the elapsed time.
//! * Optional second stream (desktop/system audio through a loopback device such as
//!   BlackHole) recorded to `system.wav`; the engine mixes both when processing.
//!   macOS has no built-in loopback input, so this requires a virtual audio device.
//! * Level (RMS/peak) events are emitted ~10×/s for the UI waveform.

use std::fs::{File, OpenOptions};
use std::io::{Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, State, Manager};

use crate::paths;
use crate::system_audio::SystemTap;

#[derive(Serialize, Deserialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct RecordingMeta {
    pub id: String,
    pub started_at: String, // RFC 3339
    pub ended_at: Option<String>,
    pub duration_sec: f64,
    pub input_device: String,
    pub file_path: String,
    #[serde(default)]
    pub system_device: Option<String>,
    #[serde(default)]
    pub system_file_path: Option<String>,
    pub format: String, // "wav/pcm16"
    pub sample_rate: u32,
    pub channels: u16,
    pub status: String, // recording | saved | failed
    pub error: Option<String>,
}

#[derive(Serialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct RecordingStatus {
    pub recording: bool,
    pub meta: Option<RecordingMeta>,
    pub elapsed_sec: f64,
}

#[derive(Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct LevelEvent {
    rms: f32,
    peak: f32,
    elapsed_sec: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    system_rms: Option<f32>,
}

enum WriterMsg {
    Samples(Vec<i16>),
    Stop,
}

/// One captured stream: cpal stream + writer thread.
struct Capture {
    _stream: cpal::Stream,
    tx: Sender<WriterMsg>,
    writer: Option<JoinHandle<Result<u64, String>>>,
    sample_rate: u32,
}

struct Active {
    meta: RecordingMeta,
    started: Instant,
    mic: Capture,
    system: Option<SystemTap>,
    stream_error: Arc<Mutex<Option<String>>>,
    stopped: Arc<AtomicBool>,
}

// cpal::Stream is !Send on some platforms; we only touch it from Tauri commands and drop it on stop.
unsafe impl Send for Active {}

#[derive(Default)]
pub struct RecorderState {
    active: Mutex<Option<Active>>,
    /// Recordings stopped from the tray or the global shortcut, waiting for the UI to submit them.
    pending: Mutex<Vec<RecordingMeta>>,
}

fn wav_header(sample_rate: u32, channels: u16, data_len: u32) -> [u8; 44] {
    let byte_rate = sample_rate * channels as u32 * 2;
    let block_align = channels * 2;
    let mut h = [0u8; 44];
    h[0..4].copy_from_slice(b"RIFF");
    h[4..8].copy_from_slice(&(36 + data_len).to_le_bytes());
    h[8..12].copy_from_slice(b"WAVE");
    h[12..16].copy_from_slice(b"fmt ");
    h[16..20].copy_from_slice(&16u32.to_le_bytes());
    h[20..22].copy_from_slice(&1u16.to_le_bytes()); // PCM
    h[22..24].copy_from_slice(&channels.to_le_bytes());
    h[24..28].copy_from_slice(&sample_rate.to_le_bytes());
    h[28..32].copy_from_slice(&byte_rate.to_le_bytes());
    h[32..34].copy_from_slice(&block_align.to_le_bytes());
    h[34..36].copy_from_slice(&16u16.to_le_bytes());
    h[36..40].copy_from_slice(b"data");
    h[40..44].copy_from_slice(&data_len.to_le_bytes());
    h
}

fn patch_header(file: &mut File, sample_rate: u32, data_len: u64) -> std::io::Result<()> {
    let len = data_len.min(u32::MAX as u64) as u32;
    let h = wav_header(sample_rate, 1, len);
    let pos = file.stream_position()?;
    file.seek(SeekFrom::Start(0))?;
    file.write_all(&h)?;
    file.seek(SeekFrom::Start(pos))?;
    Ok(())
}

fn write_meta(dir: &Path, meta: &RecordingMeta) -> std::io::Result<()> {
    let tmp = dir.join("recording.json.tmp");
    std::fs::write(&tmp, serde_json::to_vec_pretty(meta)?)?;
    std::fs::rename(tmp, dir.join("recording.json"))
}

/// Writer thread: owns the file, appends PCM16, patches the header every second.
/// `meta` is Some only for the primary (microphone) stream, which also keeps recording.json fresh.
fn writer_loop(
    rx: Receiver<WriterMsg>,
    path: PathBuf,
    dir: PathBuf,
    sample_rate: u32,
    mut meta: Option<RecordingMeta>,
    bytes_written: Arc<AtomicU64>,
) -> Result<u64, String> {
    let mut file = OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .open(&path)
        .map_err(|e| e.to_string())?;
    file.write_all(&wav_header(sample_rate, 1, 0)).map_err(|e| e.to_string())?;
    let mut data_len: u64 = 0;
    let mut last_patch = Instant::now();
    let bytes_per_sec = (sample_rate as u64) * 2;

    loop {
        match rx.recv() {
            Ok(WriterMsg::Samples(buf)) => {
                let mut bytes = Vec::with_capacity(buf.len() * 2);
                for s in &buf {
                    bytes.extend_from_slice(&s.to_le_bytes());
                }
                file.write_all(&bytes).map_err(|e| e.to_string())?;
                data_len += bytes.len() as u64;
                bytes_written.store(data_len, Ordering::Relaxed);
                if last_patch.elapsed() >= Duration::from_secs(1) {
                    patch_header(&mut file, sample_rate, data_len).map_err(|e| e.to_string())?;
                    file.flush().map_err(|e| e.to_string())?;
                    if let Some(m) = meta.as_mut() {
                        m.duration_sec = data_len as f64 / bytes_per_sec as f64;
                        let _ = write_meta(&dir, m);
                    }
                    last_patch = Instant::now();
                }
            }
            Ok(WriterMsg::Stop) | Err(_) => break,
        }
    }
    patch_header(&mut file, sample_rate, data_len).map_err(|e| e.to_string())?;
    file.sync_all().map_err(|e| e.to_string())?;
    Ok(data_len)
}

fn downmix_to_i16<T: cpal::Sample>(input: &[T], channels: usize, to_f32: impl Fn(T) -> f32) -> (Vec<i16>, f32, f32) {
    let frames = input.len() / channels.max(1);
    let mut out = Vec::with_capacity(frames);
    let mut sum_sq = 0f32;
    let mut peak = 0f32;
    for f in 0..frames {
        let mut acc = 0f32;
        for c in 0..channels {
            acc += to_f32(input[f * channels + c]);
        }
        let v = (acc / channels as f32).clamp(-1.0, 1.0);
        sum_sq += v * v;
        peak = peak.max(v.abs());
        out.push((v * i16::MAX as f32) as i16);
    }
    let rms = if frames > 0 { (sum_sq / frames as f32).sqrt() } else { 0.0 };
    (out, rms, peak)
}

fn find_device(name: Option<&str>) -> Result<cpal::Device, String> {
    let host = cpal::default_host();
    if let Some(n) = name {
        if let Ok(devs) = host.input_devices() {
            for d in devs {
                if d.name().ok().as_deref() == Some(n) {
                    return Ok(d);
                }
            }
        }
        log::warn!("input device '{}' not found; falling back to default", n);
    }
    host.default_input_device()
        .ok_or_else(|| "No microphone available. Connect a microphone or check System Settings → Privacy & Security → Microphone.".to_string())
}

/// Open a cpal input stream for `device`, writing mono PCM16 to `path`.
/// `level_cb` receives (rms, peak) roughly 10×/s.
fn start_capture(
    device: &cpal::Device,
    path: PathBuf,
    dir: PathBuf,
    meta: Option<RecordingMeta>,
    stopped: Arc<AtomicBool>,
    err_slot: Arc<Mutex<Option<String>>>,
    level_cb: Arc<dyn Fn(f32, f32) + Send + Sync>,
) -> Result<Capture, String> {
    let config = device
        .default_input_config()
        .map_err(|e| format!("Could not read audio device configuration: {e}"))?;
    let sample_rate = config.sample_rate().0;
    let in_channels = config.channels() as usize;

    let (tx, rx) = channel::<WriterMsg>();
    let bytes_written = Arc::new(AtomicU64::new(0));
    let writer = {
        let (p, d, b) = (path.clone(), dir.clone(), bytes_written.clone());
        std::thread::Builder::new()
            .name("huddle-wav-writer".into())
            .spawn(move || writer_loop(rx, p, d, sample_rate, meta, b))
            .map_err(|e| e.to_string())?
    };

    let err_fn = {
        let slot = err_slot.clone();
        move |e: cpal::StreamError| {
            log::error!("input stream error: {e}");
            if let Ok(mut g) = slot.lock() {
                *g = Some(format!("Audio stream error: {e}"));
            }
        }
    };
    let last_emit = Arc::new(Mutex::new(Instant::now()));

    macro_rules! build {
        ($t:ty, $conv:expr) => {{
            let tx = tx.clone();
            let last_emit = last_emit.clone();
            let stopped = stopped.clone();
            let level_cb = level_cb.clone();
            device.build_input_stream(
                &config.clone().into(),
                move |data: &[$t], _| {
                    if stopped.load(Ordering::Relaxed) {
                        return;
                    }
                    let (mono, rms, peak) = downmix_to_i16(data, in_channels, $conv);
                    let _ = tx.send(WriterMsg::Samples(mono));
                    if let Ok(mut le) = last_emit.try_lock() {
                        if le.elapsed() >= Duration::from_millis(100) {
                            *le = Instant::now();
                            level_cb(rms, peak);
                        }
                    }
                },
                err_fn,
                None,
            )
        }};
    }

    let stream = match config.sample_format() {
        cpal::SampleFormat::F32 => build!(f32, |v: f32| v),
        cpal::SampleFormat::I16 => build!(i16, |v: i16| v as f32 / i16::MAX as f32),
        cpal::SampleFormat::U16 => build!(u16, |v: u16| (v as f32 - 32768.0) / 32768.0),
        cpal::SampleFormat::I32 => build!(i32, |v: i32| v as f32 / i32::MAX as f32),
        other => return Err(format!("Unsupported audio sample format: {other:?}")),
    }
    .map_err(|e| format!("Could not open audio device: {e}"))?;
    stream.play().map_err(|e| format!("Could not start audio device: {e}"))?;
    Ok(Capture { _stream: stream, tx, writer: Some(writer), sample_rate })
}

fn finish_capture(mut c: Capture) -> Result<u64, String> {
    // Pause explicitly before dropping so CoreAudio releases the microphone right away
    // (the orange indicator otherwise lingers until the audio unit is torn down).
    let _ = c._stream.pause();
    drop(c._stream);
    let _ = c.tx.send(WriterMsg::Stop);
    match c.writer.take().map(|h| h.join()) {
        Some(Ok(r)) => r,
        _ => Err("Writer thread panicked".into()),
    }
}

#[tauri::command]
pub fn start_recording(
    app: AppHandle,
    device_name: Option<String>,
    system_audio: Option<bool>,
    system_device_name: Option<String>,
) -> Result<RecordingMeta, String> {
    let _ = system_device_name; // kept for API compatibility; ScreenCaptureKit needs no device
    start(&app, device_name, system_audio.unwrap_or(false))
}

pub fn is_recording(app: &AppHandle) -> bool {
    app.state::<RecorderState>().active.lock().map(|g| g.is_some()).unwrap_or(false)
}

pub fn push_pending(app: &AppHandle, meta: RecordingMeta) {
    if let Ok(mut q) = app.state::<RecorderState>().pending.lock() {
        q.push(meta);
    }
}

/// Recordings finished from the menu bar / shortcut that the UI has not yet turned into meetings.
#[tauri::command]
pub fn take_pending_recordings(state: State<'_, RecorderState>) -> Vec<RecordingMeta> {
    state.pending.lock().map(|mut q| std::mem::take(&mut *q)).unwrap_or_default()
}

/// Start capturing with `device_name` (None = system default) and optionally the system audio.
pub fn start(app: &AppHandle, device_name: Option<String>, want_system: bool) -> Result<RecordingMeta, String> {
    let state = app.state::<RecorderState>();
    let mut guard = state.active.lock().map_err(|_| "recorder lock poisoned")?;
    if guard.is_some() {
        return Err("A recording is already in progress.".into());
    }

    let device = find_device(device_name.as_deref())?;
    let dev_name = device.name().unwrap_or_else(|_| "Microphone".into());
    let sample_rate = device
        .default_input_config()
        .map_err(|e| format!("Could not read microphone configuration: {e}"))?
        .sample_rate()
        .0;

    let data_dir = paths::data_dir(app).map_err(|e| e.to_string())?;
    let id = format!(
        "{}-{}",
        chrono::Local::now().format("%Y%m%d-%H%M%S"),
        &uuid::Uuid::new_v4().simple().to_string()[..6]
    );
    let dir = data_dir.join("recordings").join(&id);
    std::fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let wav_path = dir.join("audio.wav");
    let sys_path = dir.join("system.wav");

    let meta = RecordingMeta {
        id: id.clone(),
        started_at: chrono::Utc::now().to_rfc3339(),
        ended_at: None,
        duration_sec: 0.0,
        input_device: dev_name.clone(),
        file_path: wav_path.to_string_lossy().into_owned(),
        system_device: if want_system { Some("System audio".into()) } else { None },
        system_file_path: if want_system { Some(sys_path.to_string_lossy().into_owned()) } else { None },
        format: "wav/pcm16".into(),
        sample_rate,
        channels: 1,
        status: "recording".into(),
        error: None,
    };
    write_meta(&dir, &meta).map_err(|e| e.to_string())?;

    let stopped = Arc::new(AtomicBool::new(false));
    let stream_error: Arc<Mutex<Option<String>>> = Arc::new(Mutex::new(None));
    let started = Instant::now();
    let system_tap = if want_system {
        match SystemTap::start(&sys_path) {
            Ok(t) => Some(t),
            Err(e) => return Err(if e == "permission-denied" {
                "permission-denied".to_string()
            } else { e }),
        }
    } else { None };
    let sys_level = system_tap.as_ref().map(|t| t.level.clone());

    let mic_cb: Arc<dyn Fn(f32, f32) + Send + Sync> = {
        let app = app.clone();
        Arc::new(move |rms, peak| {
            let system_rms = sys_level.as_ref().and_then(|l| l.lock().ok().map(|g| *g));
            let _ = app.emit("recording:level", LevelEvent { rms, peak, elapsed_sec: started.elapsed().as_secs_f64(), system_rms });
        })
    };
    let mic = match start_capture(&device, wav_path, dir.clone(), Some(meta.clone()), stopped.clone(), stream_error.clone(), mic_cb) {
        Ok(c) => c,
        Err(e) => { if let Some(t) = system_tap { let _ = t.stop(); } return Err(e); }
    };
    let system = system_tap;

    log::info!("recording started: {} ({} Hz{})", id, sample_rate, if system.is_some() { ", + system audio" } else { "" });
    *guard = Some(Active { meta: meta.clone(), started, mic, system, stream_error, stopped });
    drop(guard);
    crate::tray::refresh(app, true);
    Ok(meta)
}

#[tauri::command]
pub fn stop_recording(app: AppHandle) -> Result<RecordingMeta, String> {
    stop(&app)
}

pub fn stop(app: &AppHandle) -> Result<RecordingMeta, String> {
    let state = app.state::<RecorderState>();
    let mut guard = state.active.lock().map_err(|_| "recorder lock poisoned")?;
    let active = guard.take().ok_or("No recording in progress.")?;
    let elapsed = active.started.elapsed().as_secs_f64();
    active.stopped.store(true, Ordering::Relaxed);

    let mic_rate = active.mic.sample_rate;
    let mic_result = finish_capture(active.mic);
    let sys_result = active.system.map(|t| t.stop().map(|_| 0u64));

    let data_dir = paths::data_dir(app).map_err(|e| e.to_string())?;
    let dir = data_dir.join("recordings").join(&active.meta.id);
    let mut meta = active.meta.clone();
    meta.ended_at = Some(chrono::Utc::now().to_rfc3339());

    match mic_result {
        Ok(data_len) => {
            let bytes_per_sec = (mic_rate as u64) * 2;
            meta.duration_sec = if bytes_per_sec > 0 { data_len as f64 / bytes_per_sec as f64 } else { elapsed };
            meta.status = "saved".into();
        }
        Err(e) => {
            meta.status = "failed".into();
            meta.error = Some(e);
            meta.duration_sec = elapsed;
        }
    }
    if let Some(Err(e)) = sys_result {
        // Mic audio is still fine; note the system stream problem without failing the meeting.
        meta.error = Some(format!("System audio was not saved: {e}"));
        meta.system_file_path = None;
    }
    if let Ok(g) = active.stream_error.lock() {
        if let Some(e) = g.as_ref() {
            meta.error = Some(e.clone());
        }
    }
    write_meta(&dir, &meta).map_err(|e| e.to_string())?;
    log::info!("recording stopped: {} ({:.1}s, {})", meta.id, meta.duration_sec, meta.status);
    drop(guard);
    crate::tray::refresh(app, false);
    Ok(meta)
}

#[tauri::command]
pub fn recording_status(state: State<'_, RecorderState>) -> Result<RecordingStatus, String> {
    let guard = state.active.lock().map_err(|_| "recorder lock poisoned")?;
    Ok(match guard.as_ref() {
        Some(a) => RecordingStatus {
            recording: true,
            meta: Some(a.meta.clone()),
            elapsed_sec: a.started.elapsed().as_secs_f64(),
        },
        None => RecordingStatus { recording: false, meta: None, elapsed_sec: 0.0 },
    })
}

/// Recordings whose `recording.json` still says `recording` — the app died mid-meeting.
/// Quitting while recording: finish the file properly and leave it for the next launch to offer
/// as a recoverable recording (the in-memory pending queue does not survive the process).
pub fn stop_for_exit(app: &AppHandle) {
    if let Ok(mut meta) = stop(app) {
        if meta.status == "saved" {
            meta.status = "unsubmitted".into();
            if let Ok(data_dir) = paths::data_dir(app) {
                let _ = write_meta(&data_dir.join("recordings").join(&meta.id), &meta);
            }
        }
    }
}

/// Delete unfinished recordings the user chose not to recover, so the prompt does not return.
#[tauri::command]
pub fn discard_unfinished_recordings(app: AppHandle, ids: Vec<String>) -> Result<(), String> {
    let rec_dir = paths::data_dir(&app).map_err(|e| e.to_string())?.join("recordings");
    for id in ids {
        if id.is_empty() || id.contains(['/', '\\']) || id.starts_with('.') {
            continue;
        }
        let dir = rec_dir.join(&id);
        let Ok(raw) = std::fs::read(dir.join("recording.json")) else { continue };
        let Ok(meta) = serde_json::from_slice::<RecordingMeta>(&raw) else { continue };
        if meta.status == "recording" || meta.status == "unsubmitted" {
            std::fs::remove_dir_all(&dir).map_err(|e| e.to_string())?;
        }
    }
    Ok(())
}

#[tauri::command]
pub fn list_unfinished_recordings(app: AppHandle) -> Result<Vec<RecordingMeta>, String> {
    let data_dir = paths::data_dir(&app).map_err(|e| e.to_string())?;
    let rec_dir = data_dir.join("recordings");
    let mut out = Vec::new();
    let Ok(entries) = std::fs::read_dir(&rec_dir) else { return Ok(out) };
    for e in entries.flatten() {
        let meta_path = e.path().join("recording.json");
        let Ok(raw) = std::fs::read(&meta_path) else { continue };
        let Ok(mut meta) = serde_json::from_slice::<RecordingMeta>(&raw) else { continue };
        if meta.status == "recording" || meta.status == "unsubmitted" {
            if let Ok(md) = std::fs::metadata(&meta.file_path) {
                let bytes_per_sec = (meta.sample_rate as u64) * (meta.channels as u64) * 2;
                if bytes_per_sec > 0 && md.len() > 44 {
                    meta.duration_sec = (md.len() - 44) as f64 / bytes_per_sec as f64;
                }
            }
            out.push(meta);
        }
    }
    Ok(out)
}
