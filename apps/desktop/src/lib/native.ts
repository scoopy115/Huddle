// Typed wrappers around the Tauri commands (native side). The UI never calls
// `invoke` anywhere else, so the Rust boundary is documented in one place.
//
// Browser fallback (development only): when the UI runs outside Tauri
// (`npm run dev` opened in a browser) and VITE_ENGINE_URL is set, engine calls go
// straight to the engine over HTTP; native-only features are disabled.
import { invoke, convertFileSrc } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export interface AppPaths {
  dataDir: string;
  recordingsDir: string;
  logsDir: string;
}

export interface NativeComputeDevice {
  id: string;
  name: string;
  vendor: string;
  backend: string;
  memoryBytes: number | null;
  deviceType: string;
  available: boolean;
  recommended: boolean;
}

export interface NativeHardware {
  os: string;
  osVersion: string;
  arch: string;
  cpuBrand: string;
  cpuCores: number;
  memoryBytes: number;
  appleSilicon: boolean;
  devices: NativeComputeDevice[];
}

export interface InputDevice {
  id: string;
  name: string;
  isDefault: boolean;
  sampleRate: number | null;
  channels: number | null;
  isLoopback: boolean;
}

export interface RecordingMeta {
  id: string;
  startedAt: string;
  endedAt: string | null;
  durationSec: number;
  inputDevice: string;
  filePath: string;
  systemDevice?: string | null;
  systemFilePath?: string | null;
  format: string;
  sampleRate: number;
  channels: number;
  status: "recording" | "saved" | "failed";
  error: string | null;
}

export interface RecordingStatus {
  recording: boolean;
  meta: RecordingMeta | null;
  elapsedSec: number;
}

export interface LevelEvent {
  rms: number;
  peak: number;
  elapsedSec: number;
  systemRms?: number | null;
}

/** Preferences the shell keeps for itself (menu-bar mode, tray-recording defaults). */
export interface ShellPrefs {
  menuBar: boolean;
  inputDevice: string | null;
  systemAudio: boolean;
  sounds: boolean;
}

/** The shell's LAN forwarder for the network MCP server. */
export interface ProxyStatus {
  running: boolean;
  port: number | null;
  targetPort: number | null;
  error: string | null;
}

/** How an MCP client on this Mac should start Huddle's stdio MCP server. */
export interface McpCommand {
  program: string;
  args: string[];
  development: boolean;
}

export interface EngineStatus {
  state: "starting" | "ready" | "failed" | "stopped";
  port: number | null;
  message: string | null;
  command: string | null;
  logPath: string | null;
}

export interface SystemAudioSupport { supported: boolean; permission: "granted" | "denied" | "unknown"; message: string | null }
export interface LocalePrefs { locale: string | null; force24Hour: boolean | null }

export const isTauri = () => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

const DEV_ENGINE_URL: string | undefined = import.meta.env.VITE_ENGINE_URL;
const DEV_ENGINE_TOKEN: string = import.meta.env.VITE_ENGINE_TOKEN ?? "";

async function browserEngineFetch<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${DEV_ENGINE_URL}${path}`, {
    method,
    headers: { Authorization: `Bearer ${DEV_ENGINE_TOKEN}`, ...(body != null ? { "Content-Type": "application/json" } : {}) },
    body: body != null ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let value: unknown = null;
  try { value = text ? JSON.parse(text) : null; } catch { value = text; }
  if (!res.ok) {
    const detail = (value as { detail?: unknown })?.detail;
    throw `${res.status}: ${typeof detail === "string" ? detail : text}`;
  }
  return value as T;
}

const notInDesktop = () => Promise.reject("Recording requires the Huddle desktop app.");

const browser = {
  getPaths: async (): Promise<AppPaths> => {
    const h = await browserEngineFetch<{ dataDir: string }>("GET", "/health");
    return { dataDir: h.dataDir, recordingsDir: `${h.dataDir}/recordings`, logsDir: `${h.dataDir}/logs` };
  },
  detectHardware: () => browserEngineFetch<{ hardware: NativeHardware }>("GET", "/system/environment").then((e) => e.hardware),
  listInputDevices: async (): Promise<InputDevice[]> => [],
  startRecording: notInDesktop as (d: string | null, systemAudio?: boolean, systemDevice?: string | null) => Promise<RecordingMeta>,
  stopRecording: notInDesktop as () => Promise<RecordingMeta>,
  recordingStatus: async (): Promise<RecordingStatus> => ({ recording: false, meta: null, elapsedSec: 0 }),
  listUnfinishedRecordings: async (): Promise<RecordingMeta[]> => [],
  engineStatus: async (): Promise<EngineStatus> => ({ state: DEV_ENGINE_URL ? "ready" : "failed", port: null, message: DEV_ENGINE_URL ? null : "Set VITE_ENGINE_URL to run in a browser.", command: "browser dev mode", logPath: null }),
  engineRestart: async (): Promise<EngineStatus> => browser.engineStatus(),
  engineFetch: browserEngineFetch,
  onLevel: async (_cb: (e: LevelEvent) => void): Promise<UnlistenFn> => () => {},
  onEngineStatus: async (_cb: (e: EngineStatus) => void): Promise<UnlistenFn> => () => {},
  onMenu: async (_cb: (id: string) => void): Promise<UnlistenFn> => () => {},
  getMcpCommand: async (): Promise<McpCommand> => ({ program: "huddle-engine", args: ["mcp", "--data-dir", "…"], development: true }),
  audioSrc: (meetingId: string, _path: string) => `${DEV_ENGINE_URL}/meetings/${meetingId}/audio?token=${encodeURIComponent(DEV_ENGINE_TOKEN)}`,
  systemAudioSupport: async (): Promise<SystemAudioSupport> => ({ supported: false, permission: "unknown", message: "Only available in the desktop app." }),
  requestSystemAudioPermission: async (): Promise<SystemAudioSupport> => ({ supported: false, permission: "unknown", message: null }),
  openSystemAudioSettings: async () => {},
  getLocalePrefs: async (): Promise<LocalePrefs> => ({ locale: navigator.language, force24Hour: null }),
  saveTextFile: async (_path: string, _contents: string): Promise<void> => { throw new Error("Saving files is only available in the desktop app."); },
  revealInFinder: async (_path: string): Promise<void> => { throw new Error("Only available in the desktop app."); },
  networkProxyStart: async (_port: number, _targetPort: number): Promise<ProxyStatus> => ({ running: false, port: null, targetPort: null, error: "Network access needs the desktop app." }),
  networkProxyStop: async (): Promise<ProxyStatus> => ({ running: false, port: null, targetPort: null, error: null }),
  networkProxyStatus: async (): Promise<ProxyStatus> => ({ running: false, port: null, targetPort: null, error: null }),
  openFirewallSettings: async (): Promise<void> => {},
  getShellPrefs: async (): Promise<ShellPrefs> => ({ menuBar: false, inputDevice: null, systemAudio: false, sounds: true }),
  microphonePermission: async (): Promise<MicPermission> => "unknown",
  requestMicrophonePermission: async (): Promise<MicPermission> => "unknown",
  openMicrophoneSettings: async (): Promise<void> => {},
  onShellPrefsChanged: async (_cb: (p: ShellPrefs) => void): Promise<UnlistenFn> => () => {},
  setShellPrefs: async (patch: Partial<ShellPrefs>): Promise<ShellPrefs> => ({ menuBar: false, inputDevice: null, systemAudio: false, sounds: true, ...patch } as ShellPrefs),
  takePendingRecordings: async (): Promise<RecordingMeta[]> => [],
  discardUnfinishedRecordings: async (_ids: string[]): Promise<void> => {},
  appInfo: async (): Promise<AppInfo> => ({ version: "dev", build: "browser", bundlePath: null }),
  checkForUpdates: async (): Promise<UpdateCheck> => ({ currentVersion: "dev", update: null }),
  installUpdate: async (_assetUrl: string): Promise<InstallOutcome> => { throw new Error("Only available in the desktop app."); },
  onUpdateProgress: async (_cb: (p: UpdateProgress) => void): Promise<UnlistenFn> => () => {},
  copyFile: async (_src: string, _dst: string): Promise<number> => { throw new Error("Only available in the desktop app."); },
  onRecordingStarted: async (_cb: (m: RecordingMeta) => void): Promise<UnlistenFn> => () => {},
  onRecordingError: async (_cb: (message: string) => void): Promise<UnlistenFn> => () => {},
  onRecordingWarning: async (_cb: (message: string) => void): Promise<UnlistenFn> => () => {},
  onTrayShown: async (_cb: () => void): Promise<UnlistenFn> => () => {},
  trayToggleRecording: async (): Promise<void> => { throw new Error("Only available in the desktop app."); },
  trayOpenMain: async (): Promise<void> => {},
  trayHide: async (): Promise<void> => {},
  trayQuit: async (): Promise<void> => {},
  setTrayBusy: async (_busy: boolean): Promise<void> => {},
  onRecordingStopped: async (_cb: (m: RecordingMeta) => void): Promise<UnlistenFn> => () => {},
};

const desktop = {
  getPaths: () => invoke<AppPaths>("get_paths"),
  detectHardware: () => invoke<NativeHardware>("detect_hardware"),
  listInputDevices: () => invoke<InputDevice[]>("list_input_devices"),
  startRecording: (deviceName: string | null, systemAudio = false, systemDeviceName: string | null = null) =>
    invoke<RecordingMeta>("start_recording", { deviceName, systemAudio, systemDeviceName }),
  stopRecording: () => invoke<RecordingMeta>("stop_recording"),
  recordingStatus: () => invoke<RecordingStatus>("recording_status"),
  listUnfinishedRecordings: () => invoke<RecordingMeta[]>("list_unfinished_recordings"),
  engineStatus: () => invoke<EngineStatus>("engine_status"),
  engineRestart: () => invoke<EngineStatus>("engine_restart"),
  saveTextFile: (path: string, contents: string) => invoke<void>("save_text_file", { path, contents }),
  revealInFinder: (path: string) => invoke<void>("reveal_in_finder", { path }),
  networkProxyStart: (port: number, targetPort: number) => invoke<ProxyStatus>("network_proxy_start", { port, targetPort }),
  networkProxyStop: () => invoke<ProxyStatus>("network_proxy_stop"),
  networkProxyStatus: () => invoke<ProxyStatus>("network_proxy_status"),
  openFirewallSettings: () => invoke<void>("open_firewall_settings"),
  getShellPrefs: () => invoke<ShellPrefs>("get_shell_prefs"),
  microphonePermission: () => invoke<MicPermission>("microphone_permission"),
  requestMicrophonePermission: () => invoke<MicPermission>("request_microphone_permission"),
  openMicrophoneSettings: () => invoke<void>("open_microphone_settings"),
  setShellPrefs: (patch: Partial<ShellPrefs>) => invoke<ShellPrefs>("set_shell_prefs", { patch }),
  onShellPrefsChanged: (cb: (p: ShellPrefs) => void): Promise<UnlistenFn> => listen<ShellPrefs>("shell-prefs:changed", (ev) => cb(ev.payload)),
  takePendingRecordings: () => invoke<RecordingMeta[]>("take_pending_recordings"),
  discardUnfinishedRecordings: (ids: string[]) => invoke<void>("discard_unfinished_recordings", { ids }),
  copyFile: (src: string, dst: string) => invoke<number>("copy_file", { src, dst }),
  appInfo: () => invoke<AppInfo>("app_info"),
  checkForUpdates: () => invoke<UpdateCheck>("check_for_updates"),
  installUpdate: (assetUrl: string) => invoke<InstallOutcome>("install_update", { assetUrl }),
  onUpdateProgress: (cb: (p: UpdateProgress) => void): Promise<UnlistenFn> => listen<UpdateProgress>("update:progress", (ev) => cb(ev.payload)),
  onRecordingStarted: (cb: (m: RecordingMeta) => void): Promise<UnlistenFn> => listen<RecordingMeta>("recording:started", (ev) => cb(ev.payload)),
  onRecordingStopped: (cb: (m: RecordingMeta) => void): Promise<UnlistenFn> => listen<RecordingMeta>("recording:stopped", (ev) => cb(ev.payload)),
  onRecordingError: (cb: (message: string) => void): Promise<UnlistenFn> => listen<string>("recording:error", (ev) => cb(ev.payload)),
  onRecordingWarning: (cb: (message: string) => void): Promise<UnlistenFn> => listen<string>("recording:warning", (ev) => cb(ev.payload)),
  onTrayShown: (cb: () => void): Promise<UnlistenFn> => listen<void>("tray:shown", () => cb()),
  trayToggleRecording: () => invoke<void>("tray_toggle_recording"),
  trayOpenMain: () => invoke<void>("tray_open_main"),
  trayHide: () => invoke<void>("tray_hide"),
  trayQuit: () => invoke<void>("tray_quit"),
  setTrayBusy: (busy: boolean) => invoke<void>("tray_set_busy", { busy }),
  engineFetch: <T>(method: string, path: string, body?: unknown) =>
    invoke<T>("engine_fetch", { method, path, body: body ?? null }),
  onLevel: (cb: (e: LevelEvent) => void): Promise<UnlistenFn> =>
    listen<LevelEvent>("recording:level", (ev) => cb(ev.payload)),
  onEngineStatus: (cb: (e: EngineStatus) => void): Promise<UnlistenFn> =>
    listen<EngineStatus>("engine:status", (ev) => cb(ev.payload)),
  onMenu: (cb: (id: string) => void): Promise<UnlistenFn> => listen<string>("menu", (ev) => cb(ev.payload)),
  getMcpCommand: () => invoke<McpCommand>("engine_mcp_command"),
  audioSrc: (_meetingId: string, path: string) => convertFileSrc(path),
  systemAudioSupport: () => invoke<SystemAudioSupport>("system_audio_support"),
  requestSystemAudioPermission: () => invoke<SystemAudioSupport>("request_system_audio_permission"),
  openSystemAudioSettings: () => invoke<void>("open_system_audio_settings"),
  getLocalePrefs: () => invoke<LocalePrefs>("get_locale_prefs"),
};

export type MicPermission = "granted" | "denied" | "undetermined" | "unknown";
export interface AppInfo { version: string; build: string; bundlePath: string | null }
export interface UpdateInfo { version: string; notes: string; pageUrl: string; assetUrl: string | null; assetName: string | null; assetSize: number | null }
export interface UpdateCheck { currentVersion: string; update: UpdateInfo | null }
export interface UpdateProgress { phase: string; downloaded: number; total: number | null }
export interface InstallOutcome { installed: boolean; appPath: string; reason: string | null }

export const native: typeof desktop = isTauri() ? desktop : (browser as typeof desktop);
