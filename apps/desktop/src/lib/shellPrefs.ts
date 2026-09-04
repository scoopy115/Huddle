import { native } from "@/lib/native";
import type { UserSettings } from "@/types/engine";

/** Mirror the settings the shell needs without the engine (menu-bar mode, tray-recording defaults). */
export function syncShellPrefs(s: Partial<UserSettings>) {
  const patch: Record<string, unknown> = {};
  if ("general.menuBar" in s) patch.menuBar = !!s["general.menuBar"];
  if ("recording.inputDevice" in s) patch.inputDevice = s["recording.inputDevice"] ?? null;
  if ("recording.systemAudio" in s) patch.systemAudio = !!s["recording.systemAudio"];
  if ("general.sounds" in s) patch.sounds = s["general.sounds"] !== false;
  if (Object.keys(patch).length) native.setShellPrefs(patch).catch(() => {});
}
