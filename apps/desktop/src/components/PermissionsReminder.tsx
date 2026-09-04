import { useEffect, useState } from "react";
import { isTauri, native } from "@/lib/native";
import { PermissionsPanel, allGranted, usePermissions } from "@/components/PermissionsPanel";
import { Button, Dialog } from "@/components/ui";

/**
 * On every launch: raise whatever macOS prompt is still pending. The microphone prompt is asked
 * when it was never answered; the system-audio prompt is raised by creating a tap for a moment,
 * which macOS only turns into a dialog while that permission is undetermined. A refused
 * microphone is shown once per launch with the button to fix it.
 */
export function PermissionsReminder() {
  const perms = usePermissions();
  const [open, setOpen] = useState(false);
  const [decided, setDecided] = useState(false);
  useEffect(() => {
    if (!isTauri() || decided || perms.checking) return;
    setDecided(true);
    (async () => {
      let mic = perms.mic;
      if (mic === "undetermined") {
        mic = await native.requestMicrophonePermission();
        perms.setState((s) => ({ ...s, mic }));
      }
      const system = await native.requestSystemAudioPermission().catch(() => null);
      if (system) perms.setState((s) => ({ ...s, system }));
      if (mic !== "granted" || (system && system.supported && system.permission !== "granted")) setOpen(true);
    })();
  }, [perms, decided]);
  useEffect(() => { if (open && allGranted(perms)) setOpen(false); }, [open, perms]);
  return (
    <Dialog open={open} onClose={() => setOpen(false)} title="Allow Huddle to record"
      footer={<Button variant="primary" onClick={() => setOpen(false)}>{allGranted(perms) ? "Done" : "Later"}</Button>}>
      <p className="mb-3 text-muted">Huddle needs these to record meetings. Nothing is recorded until you press Record.</p>
      <PermissionsPanel perms={perms} compact />
    </Dialog>
  );
}
