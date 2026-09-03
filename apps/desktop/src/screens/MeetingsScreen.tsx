import { useMemo, useState } from "react";
import { CheckSquare, ChevronRight, Clock, FileAudio, Mic, Search as SearchIcon, Timer, Users } from "lucide-react";
import type { Meeting } from "@/types/engine";
import { fmtClock, fmtDuration, fmtRelativeDay } from "@/lib/format";
import { useNav } from "@/lib/nav";
import { cn, speakerColor } from "@/lib/utils";
import { Badge, Button, EmptyState, Input, Spinner } from "@/components/ui";
import { MeetingContextMenu, useMeetingActions } from "@/components/MeetingMenu";

const STAGE_WORD: Record<string, string> = { preprocessing: "Preparing audio", transcribing: "Transcribing", diarizing: "Detecting speakers", identifying_speakers: "Recognising voices", refining: "Applying feedback", extracting_actions: "Finding action items", summarizing: "Summarising", indexing: "Indexing" };
const STAGE_FAIL: Record<string, string> = { preprocessing: "Audio failed", transcribing: "Transcription failed", diarizing: "Speaker detection failed", identifying_speakers: "Voice recognition failed", refining: "Feedback failed", extracting_actions: "Action items failed", summarizing: "Summary failed", indexing: "Indexing failed" };

function statusBadge(m: Meeting) {
  switch (m.status) {
    case "processing": {
      if (m.jobState === "queued") return <Badge tone="neutral"><Spinner className="h-3 w-3" /> Waiting</Badge>;
      const stage = m.jobStage ? STAGE_WORD[m.jobStage] ?? m.jobStage : "Processing";
      const pct = m.jobProgress != null && m.jobProgress > 0 ? ` ${Math.round(m.jobProgress * 100)}%` : "";
      return <Badge tone="neutral" className="text-fg/80"><Spinner className="h-3 w-3 text-accent" /> {stage}{pct}</Badge>;
    }
    case "failed":
      return <Badge tone="bad">{(m.jobError && STAGE_FAIL[m.jobError]) ?? "Processing failed"}</Badge>;
    case "saved":
      return <Badge>Saved</Badge>;
    case "recording":
      return <Badge tone="accent">Recording</Badge>;
    default:
      return null;
  }
}

const initials = (name: string) => name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]!.toUpperCase()).join("");

/** Stack of coloured initials for the people in a meeting; unnamed speakers become plain discs. */
function Faces({ m }: { m: Meeting }) {
  const named = m.participants.slice(0, 4);
  const anon = Math.max(0, Math.min(4 - named.length, (m.speakerCount ?? 0) - named.length));
  if (!named.length && !anon) return null;
  return (
    <div className="flex shrink-0 -space-x-1.5">
      {named.map((p, i) => (
        <span key={p} title={p} className={cn("flex h-7 w-7 items-center justify-center rounded-full border-2 border-surface font-display text-[10.5px] font-bold text-white", speakerColor(i).solid)}>{initials(p)}</span>
      ))}
      {Array.from({ length: anon }).map((_, i) => (
        <span key={i} className="flex h-7 w-7 items-center justify-center rounded-full border-2 border-surface bg-zinc-200 text-zinc-500 dark:bg-zinc-700 dark:text-zinc-300"><Users className="h-3 w-3" /></span>
      ))}
    </div>
  );
}

export function MeetingsScreen({ meetings, loading, onImport, onChanged }: { meetings: Meeting[] | null; loading: boolean; onImport: () => void; onChanged: () => void }) {
  const { go } = useNav();
  const [filter, setFilter] = useState("");
  const [ctx, setCtx] = useState<{ m: Meeting; x: number; y: number } | null>(null);
  const actions = useMeetingActions({ onChanged });

  const groups = useMemo(() => {
    const list = (meetings ?? []).filter((m) => !filter || m.title.toLowerCase().includes(filter.toLowerCase()) || m.participants.some((p) => p.toLowerCase().includes(filter.toLowerCase())));
    const out: { label: string; items: Meeting[] }[] = [];
    for (const m of list) {
      const label = fmtRelativeDay(m.startedAt);
      const g = out[out.length - 1];
      if (g && g.label === label) g.items.push(m);
      else out.push({ label, items: [m] });
    }
    return out;
  }, [meetings, filter]);

  return (
    <div className="flex h-full flex-col">
      <header data-tauri-drag-region className="titlebar-drag flex h-[52px] shrink-0 items-center gap-3 border-b border-border px-5">
        <h1 data-tauri-drag-region className="page-title">Meetings</h1>
        <div data-tauri-drag-region className="flex-1" />
        <div className="relative w-[240px]">
          <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
          <Input className="pl-8" placeholder="Filter meetings" value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
        <Button variant="secondary" onClick={onImport}><FileAudio className="h-3.5 w-3.5" /> Import audio</Button>
      </header>

      {ctx && <MeetingContextMenu position={{ x: ctx.x, y: ctx.y }} onClose={() => setCtx(null)} onPick={(a) => actions.run(a, ctx.m)} />}
      {actions.dialogs}
      <div className="flex-1 overflow-y-auto">
        {actions.error && <div className="mx-auto max-w-[860px] px-5 pt-3 text-[12.5px] text-danger">{actions.error}</div>}
        {loading && !meetings ? (
          <div className="flex h-full items-center justify-center"><Spinner /></div>
        ) : !meetings?.length ? (
          <EmptyState
            icon={<Mic className="h-6 w-6" />}
            title="No meetings yet"
            body="Put your Mac on the table, press New Recording, and Huddle will transcribe and summarise the conversation."
            action={<Button variant="record" onClick={() => go({ kind: "record" })}><Mic className="h-4 w-4" /> New Recording</Button>}
          />
        ) : (
          <div className="mx-auto max-w-[860px] px-5 py-5">
            {groups.map((g) => (
              <section key={g.label} className="mb-6">
                <div className="mb-2 flex items-center gap-2 px-1">
                  <span className="h-[10px] w-[3px] rounded-full bg-accent" />
                  <span className="font-display text-[12px] font-bold uppercase tracking-wider text-muted">{g.label}</span>
                  <span className="text-[11px] text-muted/70">{g.items.length}</span>
                </div>
                <div className="panel overflow-hidden">
                  {g.items.map((m) => (
                    <button key={m.id} onClick={() => go({ kind: "meeting", id: m.id })}
                      onContextMenu={(e) => { e.preventDefault(); setCtx({ m, x: e.clientX, y: e.clientY }); }}
                      className={cn("group flex w-full items-center gap-4 border-b border-border px-4 py-3.5 text-left last:border-b-0 hover:bg-accent-soft/40 dark:hover:bg-fg/[0.03]", ctx?.m.id === m.id && "bg-accent-soft/40 dark:bg-fg/[0.03]")}>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="truncate font-display text-[14.5px] font-bold tracking-tight">{m.title}</span>
                          {statusBadge(m)}
                          {m.status === "ready" && m.jobError && <Badge tone="warn">{STAGE_FAIL[m.jobError] ?? "A step failed"}</Badge>}
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-x-3.5 gap-y-0.5 text-[12px] text-muted">
                          <span className="inline-flex items-center gap-1"><Clock className="h-3 w-3" />{fmtClock(m.startedAt)}</span>
                          {m.durationSec ? <span className="inline-flex items-center gap-1"><Timer className="h-3 w-3" />{fmtDuration(m.durationSec)}</span> : null}
                          {m.speakerCount ? <span className="inline-flex items-center gap-1 truncate"><Users className="h-3 w-3" />{m.participants.length ? m.participants.join(", ") : `${m.speakerCount} speakers`}</span> : null}
                          {m.openActionCount ? <span className="inline-flex items-center gap-1 text-accent"><CheckSquare className="h-3 w-3" />{m.openActionCount} open</span> : null}
                        </div>
                        {m.summaryPreview && <div className="mt-1 line-clamp-1 text-[12.5px] text-fg/60">{m.summaryPreview}</div>}
                      </div>
                      <Faces m={m} />
                      <ChevronRight className="h-4 w-4 shrink-0 text-muted/50 transition-transform group-hover:translate-x-0.5 group-hover:text-accent" />
                    </button>
                  ))}
                </div>
              </section>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
