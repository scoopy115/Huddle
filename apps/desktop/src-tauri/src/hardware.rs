//! Hardware / compute-device detection.
//!
//! Produces the generic `ComputeDevice` records the UI and engine share. Only
//! backends that are actually usable on this machine are reported. On Apple
//! Silicon that is Metal (GPU) and CPU; CoreML/MLX are added once a provider
//! that uses them is wired in. Windows backends (CUDA/Vulkan/DirectML) will be
//! detected here later without changing the shape of the record.

use serde::Serialize;
use sysinfo::System;

#[derive(Serialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct ComputeDevice {
    pub id: String,
    pub name: String,
    pub vendor: String,     // apple | nvidia | amd | intel | cpu
    pub backend: String,    // metal | coreml | mlx | cuda | vulkan | directml | openvino | cpu
    pub memory_bytes: Option<u64>,
    pub device_type: String, // gpu | npu | cpu
    pub available: bool,
    pub recommended: bool,
}

#[derive(Serialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct HardwareInfo {
    pub os: String,
    pub os_version: String,
    pub arch: String,
    pub cpu_brand: String,
    pub cpu_cores: usize,
    pub memory_bytes: u64,
    pub apple_silicon: bool,
    pub devices: Vec<ComputeDevice>,
}

fn sysctl_string(key: &str) -> Option<String> {
    let out = std::process::Command::new("sysctl").arg("-n").arg(key).output().ok()?;
    if !out.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

pub fn detect() -> HardwareInfo {
    let mut sys = System::new();
    sys.refresh_memory();
    sys.refresh_cpu_all();

    let arch = std::env::consts::ARCH.to_string();
    let os = std::env::consts::OS.to_string();
    let os_version = System::os_version().unwrap_or_default();
    let memory_bytes = sys.total_memory();
    let cpu_cores = sys.cpus().len();

    let cpu_brand = if cfg!(target_os = "macos") {
        sysctl_string("machdep.cpu.brand_string")
    } else {
        None
    }
    .or_else(|| sys.cpus().first().map(|c| c.brand().to_string()))
    .unwrap_or_else(|| "Unknown CPU".to_string());

    let apple_silicon = cfg!(target_os = "macos") && arch == "aarch64";

    let mut devices = Vec::new();
    if apple_silicon {
        // Unified memory: the GPU can address the same pool as the CPU.
        devices.push(ComputeDevice {
            id: "apple-gpu-metal".into(),
            name: format!("{} GPU", cpu_brand.trim()),
            vendor: "apple".into(),
            backend: "metal".into(),
            memory_bytes: Some(memory_bytes),
            device_type: "gpu".into(),
            available: true,
            recommended: true,
        });
    }
    devices.push(ComputeDevice {
        id: "cpu".into(),
        name: format!("CPU ({} cores)", cpu_cores),
        vendor: "cpu".into(),
        backend: "cpu".into(),
        memory_bytes: Some(memory_bytes),
        device_type: "cpu".into(),
        available: true,
        recommended: !apple_silicon,
    });

    HardwareInfo {
        os,
        os_version,
        arch,
        cpu_brand,
        cpu_cores,
        memory_bytes,
        apple_silicon,
        devices,
    }
}

#[tauri::command]
pub fn detect_hardware() -> HardwareInfo {
    detect()
}
