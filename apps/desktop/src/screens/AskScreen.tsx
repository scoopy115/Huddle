import { useEffect, useMemo, useRef, useState } from "react";
import { MessageSquareText, Send } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { Meeting, SearchHit } from "@/types/engine";
import { fmtTime } from "@/lib/format";
import { AI_MISSING_HINT, useNav } from "@/lib/nav";
import { Button, Input, Spinner } from "@/components/ui";

interface Turn { role: "user" | "assistant"; text: string; sources?: SearchHit[]; error?: boolean }

/** Suggestions built from the user's own meetings — never generic examples. */
function suggestionsFor(meetings: Meeting[]): string[] {
  const out: string[] = [];
  const recent = meetings.filter((m) => m.status === "ready").slice(0, 3);
  if (recent[0]) out.push(`What did we decide in "${recent[0].title}"?`);
  if (meetings.some((m) => m.openActionCount > 0)) out.push("Which action items are still open?");
  const person = meetings.flatMap((m) => m.participants)[0];
  if (person) out.push(`What did ${person} commit to?`);
  if (recent[1]) out.push(`Summarise "${recent[1].title}" in three sentences.`);
  if (meetings.length > 1) out.push("Which deadlines were mentioned in the last two weeks?");
  return out.slice(0, 4);
}

export function AskScreen({ meetings }: { meetings: Meeting[] }) {
  const { go, ai } = useNav();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const suggestions = useMemo(() => suggestionsFor(meetings), [meetings]);

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [turns, busy]);

  const ask = async (question: string) => {
    const text = question.trim();
    if (!text || busy) return;
    setQ("");
    setTurns((t) => [...t, { role: "user", text }]);
    setBusy(true);
    try {
      const res = await api.askAll(text);
      setTurns((t) => [...t, { role: "assistant", text: res.answer, sources: res.sources, error: !!res.error }]);
    } catch (e) {
      setTurns((t) => [...t, { role: "assistant", text: errorMessage(e), error: true }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full flex-col">
      <header data-tauri-drag-region className="titlebar-drag flex h-[52px] shrink-0 items-center gap-3 border-b border-border px-5">
        <h1 data-tauri-drag-region className="page-title">Ask</h1>
        <div data-tauri-drag-region className="flex-1" />
        {turns.length > 0 && <Button variant="ghost" size="sm" onClick={() => setTurns([])}>Clear</Button>}
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-[760px] px-6 py-6">
          {!ai.ready && (
            <div className="flex flex-col items-center gap-3 pt-16 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-fg/10 text-muted"><MessageSquareText className="h-6 w-6" /></div>
              <div className="font-display text-[17px] font-bold tracking-tight">Ask needs an AI model</div>
              <p className="max-w-md text-[13px] text-muted">{AI_MISSING_HINT} Transcripts and search keep working without one.</p>
              <Button variant="primary" size="sm" onClick={() => go({ kind: "settings", section: "models" })}>Open Models</Button>
            </div>
          )}
          {ai.ready && turns.length === 0 && (
            <div className="flex flex-col items-center gap-3 pt-16 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-accent text-white glow-accent"><MessageSquareText className="h-6 w-6 text-white" /></div>
              <div className="font-display text-[17px] font-bold tracking-tight">Ask about your meetings</div>
              <p className="max-w-md text-[13px] text-muted">
                Answers come from your transcripts, decisions and action items, with the meeting and timestamp they are based on.
              </p>
              {suggestions.length > 0 && (
                <div className="mt-3 flex flex-wrap justify-center gap-2">
                  {suggestions.map((s) => (
                    <button key={s} className="rounded-full border border-border bg-surface px-3 py-1 text-[12.5px] transition-colors hover:border-accent/50 hover:text-accent" onClick={() => ask(s)}>{s}</button>
                  ))}
                </div>
              )}
            </div>
          )}
          <div className="flex flex-col gap-4">
            {turns.map((t, i) => (
              <div key={i} className={t.role === "user" ? "flex justify-end" : "flex justify-start"}>
                <div className={t.role === "user"
                  ? "max-w-[80%] rounded-2xl rounded-br-md bg-ink px-3.5 py-2 text-[13.5px] text-ink-fg"
                  : "selectable panel max-w-[88%] rounded-2xl rounded-bl-md px-4 py-3 text-[13.5px] leading-relaxed"}>
                  <p className={t.error ? "whitespace-pre-wrap text-danger" : "whitespace-pre-wrap"}>{t.text}</p>
                  {t.sources && t.sources.length > 0 && (
                    <div className="mt-2.5 flex flex-wrap gap-1.5 border-t border-border pt-2">
                      {t.sources.slice(0, 8).map((s) => (
                        <button key={s.segmentId} className="rounded-md border border-border px-2 py-0.5 text-[11px] text-muted hover:border-accent/50 hover:text-accent"
                          onClick={() => go({ kind: "meeting", id: s.meetingId, seek: s.start, segmentId: s.segmentId })}>
                          {s.meetingTitle} · {fmtTime(s.start)}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ))}
            {busy && <div className="flex items-center gap-2 text-[12.5px] text-muted"><Spinner /> Thinking…</div>}
            <div ref={bottom} />
          </div>
        </div>
      </div>

      <div className="border-t border-border bg-surface/60 px-6 py-3">
        <div className="mx-auto flex max-w-[760px] gap-2">
          <Input autoFocus className="h-10 text-[14px]" placeholder="Ask about any meeting or action item…" value={q}
            onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ask(q)} disabled={busy} />
          <Button variant="primary" className="h-10" loading={busy} onClick={() => ask(q)}><Send className="h-4 w-4" /></Button>
        </div>
      </div>
    </div>
  );
}
