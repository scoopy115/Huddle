import { useSyncExternalStore } from "react";
import { native, type UpdateInfo, type UpdateProgress } from "@/lib/native";

/**
 * One small store for the update flow, shared by the auto-check in App, the "Check for updates"
 * button in Settings, and the dialog. Checks run on launch and every 24 hours when enabled.
 */
export interface UpdateState {
  checking: boolean;
  /** When the last check finished, and what it found. */
  checkedAt: number | null;
  currentVersion: string | null;
  available: UpdateInfo | null;
  error: string | null;
  /** True while the dialog should be visible (a check found something and it was not dismissed). */
  prompt: boolean;
  installing: UpdateProgress | null;
  installError: string | null;
  /** The unpacked new version, once downloaded. */
  unpacked: { appPath: string; folder: string } | null;
}

let state: UpdateState = { checking: false, checkedAt: null, currentVersion: null, available: null, error: null, prompt: false, installing: null, installError: null, unpacked: null };
const listeners = new Set<() => void>();
const set = (patch: Partial<UpdateState>) => { state = { ...state, ...patch }; listeners.forEach((l) => l()); };

export function useUpdates(): UpdateState {
  return useSyncExternalStore((l) => { listeners.add(l); return () => listeners.delete(l); }, () => state);
}

const DAY = 24 * 60 * 60 * 1000;
let timer: ReturnType<typeof setInterval> | null = null;

/** Ask GitHub for the latest release. Errors are kept quiet for automatic checks. */
export async function checkForUpdates({ manual = false } = {}): Promise<UpdateState> {
  if (state.checking) return state;
  set({ checking: true, error: null });
  try {
    const r = await native.checkForUpdates();
    set({ checking: false, checkedAt: Date.now(), currentVersion: r.currentVersion, available: r.update, prompt: !!r.update });
  } catch (e) {
    set({ checking: false, checkedAt: Date.now(), error: manual ? String(e instanceof Error ? e.message : e) : null });
  }
  return state;
}

/** Start the periodic checks (idempotent); `enabled=false` stops them. */
export function scheduleUpdateChecks(enabled: boolean) {
  if (timer) { clearInterval(timer); timer = null; }
  if (!enabled) return;
  if (!state.checkedAt) setTimeout(() => checkForUpdates(), 4000);
  timer = setInterval(() => checkForUpdates(), DAY);
}

/** Re-open the dialog, e.g. from the sidebar button after it was dismissed. */
export function showUpdatePrompt() { if (state.available) set({ prompt: true }); }

export function dismissUpdate() { set({ prompt: false, installError: null, unpacked: null }); }

export async function installUpdate() {
  const u = state.available;
  if (!u?.assetUrl) return;
  set({ installing: { phase: "downloading", downloaded: 0, total: u.assetSize ?? null }, installError: null, unpacked: null });
  const un = await native.onUpdateProgress((p) => set({ installing: p }));
  try {
    const out = await native.installUpdate(u.assetUrl, u.version);
    set({ installing: null, unpacked: out });
  } catch (e) {
    set({ installing: null, installError: String(e instanceof Error ? e.message : e) });
  } finally { un(); }
}
