//! The record start/stop chimes for recordings the shell starts itself (menu bar, ⌥⌘R). The UI
//! synthesizes the same two notes with Web Audio, but a hidden webview does not reliably play,
//! so the shell renders them to small WAV files once and plays them with `afplay`.

use std::path::PathBuf;

use tauri::AppHandle;

pub enum Chime {
    RecordStart,
    RecordStop,
}

const RATE: u32 = 44_100;
const PEAK: f32 = 0.28;

/// Sine notes with the UI's envelope: 5 ms exponential attack, exponential decay to silence.
fn synth(notes: &[(f32, f32, f32)]) -> Vec<i16> {
    let total = notes.iter().map(|(_, dur, at)| at + dur).fold(0.0f32, f32::max) + 0.05;
    let mut buf = vec![0f32; (total * RATE as f32) as usize];
    for &(freq, dur, at) in notes {
        let n = (dur * RATE as f32) as usize;
        let start = (at * RATE as f32) as usize;
        for i in 0..n {
            let t = i as f32 / RATE as f32;
            let env = if t < 0.005 {
                0.0001 * (PEAK / 0.0001f32).powf(t / 0.005)
            } else {
                PEAK * (0.0001 / PEAK).powf((t - 0.005) / (dur - 0.005))
            };
            if let Some(s) = buf.get_mut(start + i) {
                *s += (2.0 * std::f32::consts::PI * freq * t).sin() * env;
            }
        }
    }
    buf.iter().map(|v| (v.clamp(-1.0, 1.0) * 32767.0) as i16).collect()
}

fn wav(samples: &[i16]) -> Vec<u8> {
    let data_len = (samples.len() * 2) as u32;
    let mut out = Vec::with_capacity(44 + data_len as usize);
    out.extend_from_slice(b"RIFF");
    out.extend_from_slice(&(36 + data_len).to_le_bytes());
    out.extend_from_slice(b"WAVEfmt ");
    out.extend_from_slice(&16u32.to_le_bytes());
    out.extend_from_slice(&1u16.to_le_bytes());
    out.extend_from_slice(&1u16.to_le_bytes());
    out.extend_from_slice(&RATE.to_le_bytes());
    out.extend_from_slice(&(RATE * 2).to_le_bytes());
    out.extend_from_slice(&2u16.to_le_bytes());
    out.extend_from_slice(&16u16.to_le_bytes());
    out.extend_from_slice(b"data");
    out.extend_from_slice(&data_len.to_le_bytes());
    for s in samples {
        out.extend_from_slice(&s.to_le_bytes());
    }
    out
}

fn file(app: &AppHandle, chime: &Chime) -> Option<PathBuf> {
    let dir = crate::paths::data_dir(app).ok()?.join("sounds");
    let (name, notes): (&str, &[(f32, f32, f32)]) = match chime {
        Chime::RecordStart => ("record-start.wav", &[(523.0, 0.11, 0.0), (784.0, 0.16, 0.1)]),
        Chime::RecordStop => ("record-stop.wav", &[(784.0, 0.11, 0.0), (523.0, 0.18, 0.1)]),
    };
    let path = dir.join(name);
    if !path.exists() {
        std::fs::create_dir_all(&dir).ok()?;
        std::fs::write(&path, wav(&synth(notes))).ok()?;
    }
    Some(path)
}

/// Fire and forget; respects the "Interface sounds" setting mirrored into the shell prefs.
pub fn play(app: &AppHandle, chime: Chime) {
    if !crate::shell_prefs::load(app).sounds {
        return;
    }
    if let Some(path) = file(app, &chime) {
        let _ = std::process::Command::new("afplay").arg(path).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).spawn();
    }
}
