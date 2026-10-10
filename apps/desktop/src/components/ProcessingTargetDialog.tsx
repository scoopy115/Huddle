import { useCallback, useRef, useState, type ReactNode } from "react";
import { Laptop, Server } from "lucide-react";
import type { ProcessingTarget, UserSettings } from "@/types/engine";
import { api } from "@/lib/api";
import { Button, Dialog, Spinner } from "@/components/ui";
import { platformName } from "@/lib/utils";

/** Where a new meeting is processed: the setting decides, "ask" opens the chooser (only when a server is set up). */
export function processingChoice(settings: UserSettings): ProcessingTarget | "ask" {
  if (!settings["server.url"] || !settings["server.apiKey"]) return "local";
  return settings["server.defaultTarget"] ?? "ask";
}

const serverReachable = () => api.serverTest().then((r) => r.ok).catch(() => false);

type Pending =
  | { kind: "ask"; serverName: string; available: boolean | null }
  | { kind: "down"; serverName: string; waiting: boolean };

/** Decides where a finished recording or import is processed. `resolve()` answers with the target:
 *  immediately when the setting is clear and the server answers, otherwise through a dialog —
 *  "Process where?" (the server greyed out while it cannot be reached) or "Server not available"
 *  (wait for it, or process on this computer). Render `dialogs` once next to the caller. */
export function useProcessingTarget(): { resolve: (opts?: { unattended?: boolean }) => Promise<ProcessingTarget>; dialogs: ReactNode } {
  const [pending, setPending] = useState<Pending | null>(null);
  const done = useRef<((t: ProcessingTarget) => void) | null>(null);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  const finish = useCallback((t: ProcessingTarget) => {
    if (poll.current) { clearInterval(poll.current); poll.current = null; }
    setPending(null);
    const d = done.current; done.current = null;
    d?.(t);
  }, []);

  const resolve = useCallback(async (opts?: { unattended?: boolean }): Promise<ProcessingTarget> => {
    const s = await api.getSettings().catch(() => null);
    const choice = s ? processingChoice(s) : "local";
    if (!s || choice === "local") return "local";
    const serverName = s["server.name"] || "The server";
    // A recording nobody is watching (menu bar) never asks; "ask" means this computer.
    if (choice === "ask" && opts?.unattended) return "local";
    return new Promise<ProcessingTarget>((res) => {
      done.current = res;
      if (choice === "ask") {
        setPending({ kind: "ask", serverName, available: null });
        serverReachable().then((ok) => setPending((p) => (p?.kind === "ask" ? { ...p, available: ok } : p)));
        return;
      }
      serverReachable().then((ok) => { if (ok) finish("remote"); else setPending({ kind: "down", serverName, waiting: false }); });
    });
  }, [finish]);

  const wait = () => {
    setPending((p) => (p?.kind === "down" ? { ...p, waiting: true } : p));
    if (poll.current) return;
    poll.current = setInterval(() => { serverReachable().then((ok) => { if (ok && done.current) finish("remote"); }); }, 5000);
  };

  const dialogs = (
    <>
      <ProcessingTargetDialog open={pending?.kind === "ask"} serverName={pending?.serverName} available={pending?.kind === "ask" ? pending.available : null} onPick={finish} />
      <Dialog open={pending?.kind === "down"} onClose={() => finish("local")} title="Server not available"
        footer={
          <>
            <Button variant="ghost" onClick={() => finish("local")}>Process on this {platformName}</Button>
            {pending?.kind === "down" && !pending.waiting && <Button variant="primary" onClick={wait}>Wait</Button>}
          </>
        }>
        <p className="text-muted">
          {pending?.serverName} is not available right now.
          {pending?.kind === "down" && pending.waiting ? " Waiting for it; this continues by itself as soon as it answers." : " Wait for it, or process this meeting on this " + platformName + "."}
        </p>
        {pending?.kind === "down" && pending.waiting && <div className="mt-3 flex items-center gap-2 text-[12px] text-muted"><Spinner className="h-3.5 w-3.5" /> Waiting for the server…</div>}
      </Dialog>
    </>
  );
  return { resolve, dialogs };
}

/** "Process where?" — asked after Stop and after Import Audio when the setting says ask.
 *  Closing the dialog means "here". `available` null = still checking, false = server greyed out. */
export function ProcessingTargetDialog({ open, serverName, available, onPick }: { open: boolean; serverName?: string | null; available: boolean | null; onPick: (target: ProcessingTarget) => void }) {
  return (
    <Dialog open={open} onClose={() => onPick("local")} title="Process where?" width={440}>
      <div className="grid grid-cols-2 gap-2">
        <button className="pressable flex flex-col items-start gap-1.5 rounded-xl border border-border p-3 text-left hover:border-fg/30" onClick={() => onPick("local")}>
          <Laptop className="h-4 w-4 text-muted" />
          <span className="text-[13px] font-medium">This {platformName}</span>
          <span className="text-[11.5px] text-muted">Everything stays here.</span>
        </button>
        <button disabled={available !== true} className="pressable flex flex-col items-start gap-1.5 rounded-xl border border-accent/40 bg-accent-soft/40 p-3 text-left hover:border-accent disabled:cursor-not-allowed disabled:border-border disabled:bg-transparent disabled:opacity-55 disabled:hover:border-border" onClick={() => onPick("remote")}>
          <Server className={available === true ? "h-4 w-4 text-accent" : "h-4 w-4 text-muted"} />
          <span className="text-[13px] font-medium">{serverName || "The server"}</span>
          <span className="text-[11.5px] text-muted">{available === null ? "Checking…" : available ? "Sent over TLS, result comes back here." : "Not available right now."}</span>
        </button>
      </div>
      <p className="mt-3 text-[11.5px] text-muted">Change the default under Settings → Server.</p>
    </Dialog>
  );
}
