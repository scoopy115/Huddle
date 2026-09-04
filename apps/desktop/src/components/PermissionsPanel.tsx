import { useCallback, useEffect, useState } from "react";
import { Check, Mic, Monitor } from "lucide-react";
import { native, type MicPermission, type SystemAudioSupport } from "@/lib/native";
import { Button } from "@/components/ui";

export interface PermissionState { mic: MicPermission; system: SystemAudioSupport | null; checking: boolean }

/**
 * The microphone state comes from macOS. For system audio macOS offers no query at all (a tap
 * without permission just delivers silence), so the panel only points to System Settings; the
 * one-time macOS prompt is raised by creating a tap (`requestSystemAudioPermission`).
 */
export function usePermissions() {
  const [state, setState] = useState<PermissionState>({ mic: "unknown", system: null, checking: true });
  const refresh = useCallback(async () => {
    setState((s) => ({ ...s, checking: true }));
    const [mic, system] = await Promise.all([native.microphonePermission().catch(() => "unknown" as MicPermission), native.systemAudioSupport().catch(() => null)]);
    setState({ mic, system, checking: false });
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  useEffect(() => {
    // Coming back from System Settings: check again while the microphone is still missing.
    if (state.mic === "granted") return;
    const onFocus = () => { refresh(); };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refresh, state.mic]);
  return { ...state, refresh, setState };
}

export const micGranted = (p: PermissionState) => p.mic === "granted";

export function PermissionsPanel({ perms, compact = false }: { perms: ReturnType<typeof usePermissions>; compact?: boolean }) {
  const [busy, setBusy] = useState(false);
  const askMic = async () => { setBusy(true); try { const mic = await native.requestMicrophonePermission(); perms.setState((s) => ({ ...s, mic })); } finally { setBusy(false); } };
  const micOk = perms.mic === "granted";
  const sysSupported = perms.system?.supported !== false;
  const row = compact ? "flex items-center gap-3 px-3.5 py-2.5" : "flex items-center gap-3 px-4 py-3";
  return (
    <div className="panel overflow-hidden">
      <div className={`${row} border-b border-border`}>
        <Mic className="h-4 w-4 shrink-0 text-muted" />
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] font-medium">Microphone</div>
          <div className="text-[12px] text-muted">{micOk ? "Allowed" : perms.mic === "denied" ? "Turned off. Allow Huddle under Privacy & Security → Microphone." : "Needed for every recording."}</div>
        </div>
        {micOk ? <Check className="h-4 w-4 text-emerald-600" />
          : perms.mic === "denied" ? <Button size="sm" onClick={() => native.openMicrophoneSettings()}>Open System Settings</Button>
          : <Button size="sm" variant="primary" loading={busy} onClick={askMic}>Allow</Button>}
      </div>
      <div className={row}>
        <Monitor className="h-4 w-4 shrink-0 text-muted" />
        <div className="min-w-0 flex-1">
          <div className="text-[13.5px] font-medium">System audio</div>
          <div className="text-[12px] text-muted">
            {sysSupported ? "macOS asks once, the first time Huddle records other apps. Managed under Privacy & Security → System Audio Recording." : (perms.system?.message ?? "Needs macOS 14.2 or newer.")}
          </div>
        </div>
        {sysSupported && <Button size="sm" onClick={() => native.openSystemAudioSettings()}>Open System Settings</Button>}
      </div>
    </div>
  );
}
