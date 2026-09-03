import { useEffect, useMemo, useRef, useState } from "react";
import { CornerDownLeft, Mic, Search as SearchIcon } from "lucide-react";
import { api } from "@/lib/api";
import type { Meeting, SearchHit } from "@/types/engine";
import { fmtDate, fmtTime } from "@/lib/format";
import { useNav } from "@/lib/nav";
import { cn } from "@/lib/utils";

type Row =
  | { kind: "search" }
  | { kind: "meeting"; m: Meeting }
  | { kind: "hit"; h: SearchHit };

/** ⌘K: quick jump to a meeting or a transcript moment; Enter opens the full Search page. */
export function CommandPalette({ open, onClose, meetings }: { open: boolean; onClose: () => void; meetings: Meeting[] }) {
  const { go } = useNav();
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => { if (open) { setQ(""); setHits([]); setSel(0); setTimeout(() => input.current?.focus(), 0); } }, [open]);

  useEffect(() => {
    if (!q.trim()) { setHits([]); return; }
    const t = setTimeout(() => api.search(q).then((h) => setHits(h.slice(0, 5))).catch(() => setHits([])), 160);
    return () => clearTimeout(t);
  }, [q]);

  const rows = useMemo<Row[]>(() => {
    const needle = q.trim().toLowerCase();
    const ms = (needle ? meetings.filter((m) => m.title.toLowerCase().includes(needle) || m.participants.some((p) => p.toLowerCase().includes(needle))) : meetings).slice(0, needle ? 4 : 6);
    return [...(needle ? [{ kind: "search" } as Row] : []), ...ms.map((m) => ({ kind: "meeting", m }) as Row), ...hits.map((h) => ({ kind: "hit", h }) as Row)];
  }, [q, meetings, hits]);

  useEffect(() => { setSel(0); }, [q, hits.length]);

  const activate = (r: Row | undefined) => {
    if (!r) return;
    onClose();
    if (r.kind === "search") go({ kind: "search", query: q.trim(), nonce: Date.now() });
    else if (r.kind === "meeting") go({ kind: "meeting", id: r.m.id });
    else go({ kind: "meeting", id: r.h.meetingId, seek: r.h.start, segmentId: r.h.segmentId });
  };

  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/30 pt-[14vh] backdrop-blur-[2px]" onMouseDown={onClose}>
      <div className="animate-rise panel w-[600px] max-w-[92vw] overflow-hidden shadow-2xl" onMouseDown={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-3 border-b border-border px-4">
          <SearchIcon className="h-4 w-4 shrink-0 text-accent" />
          <input
            ref={input}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              else if (e.key === "ArrowDown") { e.preventDefault(); setSel((s) => Math.min(rows.length - 1, s + 1)); }
              else if (e.key === "ArrowUp") { e.preventDefault(); setSel((s) => Math.max(0, s - 1)); }
              else if (e.key === "Enter") activate(rows[sel] ?? (q.trim() ? { kind: "search" } : undefined));
            }}
            placeholder="Search meetings and transcripts…"
            className="selectable h-12 flex-1 bg-transparent text-[15px] outline-none placeholder:text-muted"
          />
        </div>
        <div className="max-h-[50vh] overflow-y-auto p-1.5">
          {!q.trim() && meetings.length > 0 && <div className="px-2.5 pb-1 pt-1.5 font-display text-[11px] font-bold uppercase tracking-wider text-muted">Recent meetings</div>}
          {rows.map((r, i) => {
            const active = i === sel;
            const cls = cn("flex w-full items-center gap-3 rounded-lg px-2.5 py-2 text-left text-[13px]", active ? "bg-accent-soft text-fg" : "hover:bg-fg/[0.04]");
            if (r.kind === "search") return (
              <button key="search" className={cls} onMouseEnter={() => setSel(i)} onClick={() => activate(r)}>
                <SearchIcon className={cn("h-4 w-4", active ? "text-accent" : "text-muted")} />
                <span className="flex-1">Search all transcripts for <b>“{q.trim()}”</b></span>
                <CornerDownLeft className="h-3.5 w-3.5 text-muted" />
              </button>
            );
            if (r.kind === "meeting") return (
              <button key={r.m.id} className={cls} onMouseEnter={() => setSel(i)} onClick={() => activate(r)}>
                <Mic className={cn("h-4 w-4", active ? "text-accent" : "text-muted")} />
                <span className="flex-1 truncate font-medium">{r.m.title}</span>
                <span className="text-[12px] text-muted">{fmtDate(r.m.startedAt)}</span>
              </button>
            );
            return (
              <button key={r.h.segmentId} className={cn(cls, "items-start")} onMouseEnter={() => setSel(i)} onClick={() => activate(r)}>
                <span className="mt-[3px] font-mono text-[11px] text-muted">{fmtTime(r.h.start)}</span>
                <span className="min-w-0 flex-1">
                  <span className="line-clamp-1 text-fg/85" dangerouslySetInnerHTML={{ __html: escapeSnippet(r.h.snippet) }} />
                  <span className="text-[11.5px] text-muted">{r.h.meetingTitle}{r.h.speakerName ? ` · ${r.h.speakerName}` : ""}</span>
                </span>
              </button>
            );
          })}
          {q.trim() && rows.length === 1 && <div className="px-2.5 py-2 text-[12.5px] text-muted">No meeting titles match; press Enter to search the transcripts.</div>}
        </div>
        <div className="flex items-center gap-4 border-t border-border px-4 py-2 text-[11px] text-muted">
          <span><kbd className="rounded border border-border px-1">↑</kbd> <kbd className="rounded border border-border px-1">↓</kbd> navigate</span>
          <span><kbd className="rounded border border-border px-1">↵</kbd> open</span>
          <span><kbd className="rounded border border-border px-1">esc</kbd> close</span>
        </div>
      </div>
    </div>
  );
}

function escapeSnippet(s: string) {
  const esc = s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc.replace(/\[([^\]]+)\]/g, '<mark class="rounded bg-accent-soft px-0.5 text-accent">$1</mark>');
}
