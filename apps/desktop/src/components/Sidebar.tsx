import { Activity, ArrowUpCircle, CheckSquare, MessageSquareText, Mic, Search, Settings, type LucideIcon } from "lucide-react";
import { cn, modKey } from "@/lib/utils";
import { sounds } from "@/lib/sounds";
import { showUpdatePrompt, useUpdates } from "@/lib/updates";
import { useNav, type View } from "@/lib/nav";
import type { EngineStatus } from "@/lib/native";
import logo from "@/assets/huddle-logo.svg";

/** Shown once a check has found a newer release and the popup was clicked away. */
function UpdateAvailable() {
  const u = useUpdates();
  if (!u.available || u.prompt) return null;
  return (
    <button onClick={() => { sounds.open(); showUpdatePrompt(); }}
      className="pressable mb-2 flex w-full items-center justify-center gap-2 rounded-full border border-accent/40 bg-accent/10 px-3 py-2 text-[12.5px] font-semibold text-accent hover:bg-accent/15">
      <ArrowUpCircle className="h-4 w-4 text-accent" /> Update available
    </button>
  );
}

function NavItem({ icon: Icon, label, active, onClick, count, hint }: { icon: LucideIcon; label: string; active: boolean; onClick: () => void; count?: number; hint?: string }) {
  return (
    <button
      onClick={() => { if (!active) sounds.nav(); onClick(); }}
      className={cn(
        "pressable group relative flex w-full items-center gap-2.5 rounded-lg px-2.5 py-[7px] text-[13px] font-medium",
        active ? "bg-surface text-fg shadow-[0_1px_2px_rgb(28_25_24/0.06)] dark:bg-fg/[0.07] dark:shadow-none" : "text-fg/65 hover:bg-fg/[0.04] hover:text-fg",
      )}
    >
      {/* Active marker: the brand's red tick, like the one in section titles. */}
      <span className={cn("absolute -left-2 top-1/2 h-[14px] w-[3px] -translate-y-1/2 rounded-full bg-accent transition-opacity", active ? "opacity-100" : "opacity-0")} />
      <Icon className={cn("h-4 w-4", active ? "text-accent" : "opacity-70")} />
      <span className="flex-1 text-left">{label}</span>
      {count ? <span className={cn("rounded-full px-1.5 text-[11px] tabular-nums", active ? "bg-accent-soft text-accent" : "bg-fg/[0.07] text-muted")}>{count}</span> : null}
      {/* Shortcut hint, revealed on hover so the list stays calm */}
      {hint && !count ? <kbd className="rounded border border-border px-1 font-sans text-[10.5px] text-muted/80 opacity-0 transition-opacity group-hover:opacity-100">{hint}</kbd> : null}
    </button>
  );
}

export function Sidebar({ engine, openActions, recording, running }: { engine: EngineStatus; openActions: number; recording: boolean; running: number }) {
  const { view, go } = useNav();
  const is = (k: View["kind"]) => view.kind === k || (k === "meetings" && view.kind === "meeting");
  const offline = engine.state === "failed" || engine.state === "stopped";

  return (
    <aside className="relative flex h-full w-[224px] shrink-0 flex-col border-r border-border bg-sidebar">
      {/* Soft red wash behind the wordmark */}
      <div className="pointer-events-none absolute inset-x-0 top-0 h-40 bg-[radial-gradient(70%_60%_at_30%_0%,rgb(var(--accent)/0.12),transparent_70%)]" />
      <div data-tauri-drag-region className="titlebar-drag relative h-[38px] shrink-0" />
      <div data-tauri-drag-region className="titlebar-drag relative flex items-center justify-center px-4 pb-8 pt-2">
        <img data-tauri-drag-region src={logo} alt="Huddle" className="h-9 w-auto select-none" draggable={false} />
      </div>
      <nav className="relative flex flex-col gap-0.5 px-3">
        <NavItem icon={Mic} label="Meetings" active={is("meetings")} onClick={() => go({ kind: "meetings" })} hint={`${modKey}1`} />
        <NavItem icon={MessageSquareText} label="Ask" active={is("ask")} onClick={() => go({ kind: "ask" })} hint={`${modKey}2`} />
        <NavItem icon={CheckSquare} label="Action Items" active={is("actions")} onClick={() => go({ kind: "actions" })} count={openActions} hint={`${modKey}3`} />
        <NavItem icon={Activity} label="Processes" active={is("processes")} onClick={() => go({ kind: "processes" })} count={running} hint={`${modKey}4`} />
        <NavItem icon={Search} label="Search" active={is("search")} onClick={() => go({ kind: "search" })} hint={`${modKey}K`} />
        <NavItem icon={Settings} label="Settings" active={is("settings")} onClick={() => go({ kind: "settings" })} hint={`${modKey},`} />
      </nav>
      <div data-tauri-drag-region className="flex-1" />
      <div className="relative px-3 pb-3">
        <UpdateAvailable />
        <button
          onClick={() => go({ kind: "record" })}
          className={cn(
            "pressable flex w-full items-center justify-center gap-2 rounded-full bg-record px-3 py-2.5 text-[13px] font-semibold text-white glow-accent",
            !recording && "hover:brightness-110",
          )}
        >
          {recording ? <span className="h-2.5 w-2.5 rounded-full bg-white animate-record" /> : <Mic className="h-4 w-4 text-white" />}
          {recording ? "Recording…" : "New Recording"}
        </button>
        {offline && (
          <div className="mt-2 flex items-center gap-2 px-2.5 py-1.5 text-[11px] text-danger">
            <span className="h-1.5 w-1.5 rounded-full bg-danger" /> Engine offline
          </div>
        )}
      </div>
    </aside>
  );
}
