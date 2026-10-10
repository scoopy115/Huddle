import { Laptop, Server } from "lucide-react";
import type { ProcessingTarget, UserSettings } from "@/types/engine";
import { Dialog } from "@/components/ui";
import { platformName } from "@/lib/utils";

/** Where a new meeting is processed: the setting decides, "ask" opens the chooser (only when a server is set up). */
export function processingChoice(settings: UserSettings): ProcessingTarget | "ask" {
  if (!settings["server.url"] || !settings["server.apiKey"]) return "local";
  return settings["server.defaultTarget"] ?? "ask";
}

/** "Process where?" — asked after Stop and after Import Audio when the setting says ask.
 *  Closing the dialog means "here". */
export function ProcessingTargetDialog({ open, serverName, onPick }: { open: boolean; serverName?: string | null; onPick: (target: ProcessingTarget) => void }) {
  return (
    <Dialog open={open} onClose={() => onPick("local")} title="Process where?" width={440}>
      <div className="grid grid-cols-2 gap-2">
        <button className="pressable flex flex-col items-start gap-1.5 rounded-xl border border-border p-3 text-left hover:border-fg/30" onClick={() => onPick("local")}>
          <Laptop className="h-4 w-4 text-muted" />
          <span className="text-[13px] font-medium">This {platformName}</span>
          <span className="text-[11.5px] text-muted">Everything stays here.</span>
        </button>
        <button className="pressable flex flex-col items-start gap-1.5 rounded-xl border border-accent/40 bg-accent-soft/40 p-3 text-left hover:border-accent" onClick={() => onPick("remote")}>
          <Server className="h-4 w-4 text-accent" />
          <span className="text-[13px] font-medium">{serverName || "The server"}</span>
          <span className="text-[11.5px] text-muted">Sent over TLS, result comes back here.</span>
        </button>
      </div>
      <p className="mt-3 text-[11.5px] text-muted">Change the default under Settings → Server.</p>
    </Dialog>
  );
}
