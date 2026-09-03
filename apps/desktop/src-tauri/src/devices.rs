//! Audio input device enumeration via cpal (CoreAudio on macOS, WASAPI on Windows).

use cpal::traits::{DeviceTrait, HostTrait};
use serde::Serialize;

#[derive(Serialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct InputDevice {
    pub id: String,
    pub name: String,
    pub is_default: bool,
    pub sample_rate: Option<u32>,
    pub channels: Option<u16>,
    /// Looks like a virtual loopback device that carries the desktop/system audio
    /// (BlackHole, Loopback, Soundflower, an Aggregate device, …).
    pub is_loopback: bool,
}

const LOOPBACK_HINTS: &[&str] = &[
    "blackhole", "loopback", "soundflower", "aggregate", "ishowu", "virtual", "vb-cable", "vb-audio",
    "stereo mix", "what u hear", "wasapi loopback",
];

pub fn looks_like_loopback(name: &str) -> bool {
    let n = name.to_lowercase();
    LOOPBACK_HINTS.iter().any(|h| n.contains(h))
}

pub fn list() -> Vec<InputDevice> {
    let host = cpal::default_host();
    let default_name = host.default_input_device().and_then(|d| d.name().ok());
    let mut out = Vec::new();
    if let Ok(devices) = host.input_devices() {
        for d in devices {
            let name = match d.name() {
                Ok(n) => n,
                Err(_) => continue,
            };
            let cfg = d.default_input_config().ok();
            out.push(InputDevice {
                id: name.clone(),
                is_default: Some(&name) == default_name.as_ref(),
                sample_rate: cfg.as_ref().map(|c| c.sample_rate().0),
                channels: cfg.as_ref().map(|c| c.channels()),
                is_loopback: looks_like_loopback(&name),
                name,
            });
        }
    }
    // Default device first, then alphabetical — stable for the settings dropdown.
    out.sort_by(|a, b| b.is_default.cmp(&a.is_default).then(a.name.cmp(&b.name)));
    out
}

#[tauri::command]
pub fn list_input_devices() -> Vec<InputDevice> {
    list()
}
