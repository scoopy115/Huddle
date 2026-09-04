import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Calendar, CalendarClock, Check, CheckCircle2, Circle, Clock, Languages, MessageSquareText, MoreHorizontal, Pencil, Plus, Sparkles, Timer, Trash2, User, Wand2, X } from "lucide-react";
import { RichTextEditor } from "@/components/RichTextEditor";
import { api, errorMessage } from "@/lib/api";
import type { ActionItem, AskResult, MeetingDetail, MeetingSpeaker } from "@/types/engine";
import { fmtClock, fmtDate, fmtDuration, fmtDueDate, fmtTalkTime, fmtTime, languageName } from "@/lib/format";
import { useNav } from "@/lib/nav";
import { cn, speakerColor } from "@/lib/utils";
import { AudioPlayer, type PlayerHandle } from "@/components/AudioPlayer";
import { ProcessingStatus } from "@/components/ProcessingStatus";
import { TranscriptView, speakerDisplay } from "@/components/TranscriptView";
import { MeetingMenuList, useMeetingActions } from "@/components/MeetingMenu";
import { Badge, Button, Dialog, Input, SectionTitle, Spinner } from "@/components/ui";

const langLabel = (code: string | null) =>
  (code ?? "").split(",").filter(Boolean).map((c) => languageName(c)).join(", ");

export function MeetingScreen({ id, seek, segmentId, nonce, onChanged }: { id: string; seek?: number; segmentId?: number; nonce?: number; onChanged: () => void }) {
  const { go } = useNav();
  const [d, setD] = useState<MeetingDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [time, setTime] = useState(0);
  const [activeSeg, setActiveSeg] = useState<number | null>(segmentId ?? null);
  const player = useRef<PlayerHandle | null>(null);
  const [renaming, setRenaming] = useState<MeetingSpeaker | null>(null);
  const [menu, setMenu] = useState(false);
  const [ask, setAsk] = useState("");
  const [askResult, setAskResult] = useState<AskResult | null>(null);
  const [asking, setAsking] = useState(false);
  const [editingTitle, setEditingTitle] = useState(false);
  const [editingItem, setEditingItem] = useState<number | "new" | null>(null);
  const [generating, setGenerating] = useState(false);
  const [refine, setRefine] = useState(false);
  const [contextHtml, setContextHtml] = useState("");
  const pendingSeek = useRef<number | undefined>(seek);

  const load = useCallback(async () => {
    try { setD(await api.getMeeting(id)); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, [id]);

  const actions = useMeetingActions({ onChanged: () => { load(); onChanged(); }, onDeleted: () => go({ kind: "meetings" }) });

  useEffect(() => { setD(null); load(); }, [load]);

  const processing = d?.job && (d.job.state === "running" || d.job.state === "queued");
  useEffect(() => {
    if (!processing) return;
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [processing, load]);
  useEffect(() => { if (d && !processing) onChanged(); }, [processing]); // oxlint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { pendingSeek.current = seek; setActiveSeg(segmentId ?? null); }, [seek, segmentId, nonce]);
  useEffect(() => {
    if (d?.audioPath && pendingSeek.current != null) {
      const t = pendingSeek.current;
      pendingSeek.current = undefined;
      setTimeout(() => player.current?.seekTo(t, false), 150);
    }
  }, [d?.audioPath, nonce]);

  const seekTo = (t: number) => { setActiveSeg(null); player.current?.seekTo(t, true); };
  const jump = (start: number | null, seg: number | null) => { if (start != null) { setActiveSeg(seg); player.current?.seekTo(start, true); } };
  const retry = async (stage: string) => { try { await api.retryStage(id, stage); load(); } catch (e) { setError(errorMessage(e)); } };

  const toggleItem = async (a: ActionItem) => {
    setD((x) => x && { ...x, actionItems: x.actionItems.map((i) => (i.id === a.id ? { ...i, done: !i.done } : i)) });
    try { await api.updateActionItem(a.id, { done: !a.done }); onChanged(); } catch (e) { setError(errorMessage(e)); load(); }
  };
  const deleteItem = async (a: ActionItem) => {
    setD((x) => x && { ...x, actionItems: x.actionItems.filter((i) => i.id !== a.id) });
    try { await api.deleteActionItem(a.id); onChanged(); } catch (e) { setError(errorMessage(e)); load(); }
  };
  const saveItem = async (a: ActionItem | null, body: { text: string; owner: string | null; dueDate: string | null }) => {
    try {
      if (a) await api.updateActionItem(a.id, body);
      else await api.createActionItem(id, body);
      setEditingItem(null);
      await load();
      onChanged();
    } catch (e) { setError(errorMessage(e)); }
  };

  // Both start a tracked job; the meeting keeps polling while it runs (see `processing`).
  const generateItems = async () => {
    setGenerating(true);
    try { await api.generateActionItems(id); await load(); onChanged(); } catch (e) { setError(errorMessage(e)); } finally { setGenerating(false); }
  };
  const applyRefine = async () => {
    setRefine(false);
    try { await api.refine(id, contextHtml); await load(); onChanged(); } catch (e) { setError(errorMessage(e)); }
  };

  const doAsk = async () => {
    if (!ask.trim()) return;
    setAsking(true);
    try { setAskResult(await api.askMeeting(id, ask)); } catch (e) { setError(errorMessage(e)); } finally { setAsking(false); }
  };

  if (error && !d) return <div className="p-8 text-[13px] text-danger">{error}</div>;
  if (!d) return <div className="flex h-full items-center justify-center"><Spinner /></div>;
  const m = d.meeting;
  const suggestions = d.speakers.filter((s) => s.suggestedSpeakerId && !s.speakerId && !s.displayName);
  const stageRunning = (s: string) => !!d.job && (d.job.state === "running" || d.job.state === "queued") && d.job.stages[s]?.status !== "done" && d.job.stages[s]?.status !== "skipped" && d.job.stages[s]?.status !== "failed";
  const extracting = stageRunning("extracting_actions");
  const refining = stageRunning("refining");

  return (
    <div className="flex h-full flex-col">
      <header data-tauri-drag-region className="titlebar-drag flex h-[52px] shrink-0 items-center gap-2 border-b border-border px-4">
        <Button variant="ghost" size="sm" onClick={() => go({ kind: "meetings" })}><ArrowLeft className="h-4 w-4" /></Button>
        <div data-tauri-drag-region className="flex-1" />
        <div className="relative">
          <Button variant="ghost" size="sm" onClick={() => setMenu(!menu)}><MoreHorizontal className="h-4 w-4" /></Button>
          {menu && (
            <div className="animate-rise absolute right-0 top-8 z-20 w-[220px] panel p-1 shadow-xl" onMouseLeave={() => setMenu(false)}>
              <MeetingMenuList onPick={(a) => { setMenu(false); actions.run(a, m); }} />
            </div>
          )}
        </div>
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[760px] px-6 py-6">
          {editingTitle ? (
            <Input autoFocus defaultValue={m.title} className="mb-1 h-9 text-[22px] font-semibold"
              onBlur={async (e) => { setEditingTitle(false); const t = e.target.value.trim(); if (t && t !== m.title) { await api.updateMeeting(id, { title: t }); load(); onChanged(); } }}
              onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); if (e.key === "Escape") setEditingTitle(false); }} />
          ) : (
            <h1 className="selectable mb-2 cursor-text font-display text-[26px] font-bold leading-tight tracking-tight" onDoubleClick={() => setEditingTitle(true)} title="Double-click to rename">{m.title}</h1>
          )}

          {/* Meta row with icons */}
          <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-muted">
            <span className="inline-flex items-center gap-1.5"><Calendar className="h-3.5 w-3.5" />{fmtDate(m.startedAt)}</span>
            <span className="inline-flex items-center gap-1.5"><Clock className="h-3.5 w-3.5" />{fmtClock(m.startedAt)}</span>
            {m.durationSec ? <span className="inline-flex items-center gap-1.5"><Timer className="h-3.5 w-3.5" />{fmtDuration(m.durationSec)}</span> : null}
            {m.language && <button className="inline-flex items-center gap-1.5 hover:text-fg" title="Wrong language? Click to change" onClick={() => actions.run("language", m)}><Languages className="h-3.5 w-3.5" />{langLabel(m.language)}</button>}
          </div>

          {/* Speaker chips */}
          {d.speakers.length > 0 && (
            <div className="mb-4 flex flex-wrap items-center gap-1.5">
              {d.speakers.map((s) => {
                const c = speakerColor(s.colorIndex);
                const named = !!(s.displayName || s.speakerName);
                return (
                  <button key={s.id} onClick={() => setRenaming(s)}
                    title={s.nameSource === "inferred" ? "Named from the conversation — click to change" : s.nameSource === "recognized" ? "Recognised by voice — click to change" : "Click to rename"}
                    className={cn("group inline-flex items-center gap-1.5 rounded-full border py-1 pl-1.5 pr-2.5 text-[12.5px] font-medium transition-colors", named ? cn("border-transparent", c.bg, c.text, "hover:border-current/30") : "border-border bg-surface text-muted hover:border-fg/30")}>
                    <span className={cn("flex h-5 w-5 items-center justify-center rounded-full font-display text-[10px] font-bold text-white", c.solid)}>{named ? speakerDisplay(s).slice(0, 1).toUpperCase() : s.label.replace(/\D/g, "") || "?"}</span>
                    {speakerDisplay(s)}
                    {s.nameSource === "inferred" && <span className="text-[10px] text-muted">auto</span>}
                    <Pencil className="h-3 w-3 text-muted opacity-0 transition-opacity group-hover:opacity-100" />
                  </button>
                );
              })}
            </div>
          )}

          <AudioPlayer meetingId={id} path={d.audioPath} durationHint={m.durationSec} onTime={setTime} handleRef={player} />

          {(error || actions.error) && <div className="mt-3 rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-[12.5px] text-danger">{error ?? actions.error}</div>}

          {d.job && d.job.state !== "ready" && <div className="mt-4"><ProcessingStatus job={d.job} onRetry={retry} onCancel={async () => { await api.cancelProcessing(id); setTimeout(load, 800); }} /></div>}

          {suggestions.length > 0 && (
            <div className="mt-4 rounded-xl border border-accent/30 bg-accent-soft/60 p-3">
              <div className="mb-2 text-[12px] font-semibold text-accent">Voice recognised — possible match</div>
              {suggestions.map((s) => (
                <div key={s.id} className="flex items-center gap-3 py-1 text-[13px]">
                  <span className="font-medium">{s.label}</span>
                  <span className="text-muted">could be</span>
                  <span className="font-medium">{s.suggestedSpeakerName}</span>
                  <span className="text-[11.5px] text-muted">{Math.round((s.suggestedConfidence ?? 0) * 100)}% confidence</span>
                  <div className="flex-1" />
                  <Button size="sm" variant="primary" onClick={async () => { await api.confirmSpeaker(id, s.id); load(); onChanged(); }}><Check className="h-3 w-3" /> Confirm</Button>
                  <Button size="sm" variant="ghost" onClick={() => setRenaming(s)}>Choose another</Button>
                </div>
              ))}
            </div>
          )}

          {d.segments.length > 0 && d.summary && !processing && (
            <section className="mt-5">
              <SectionTitle>Ask this meeting</SectionTitle>
              <div className="flex gap-2">
                <Input placeholder="What did we decide about the homepage?" value={ask} onChange={(e) => setAsk(e.target.value)} onKeyDown={(e) => e.key === "Enter" && doAsk()} />
                <Button variant="primary" loading={asking} onClick={doAsk}><MessageSquareText className="h-3.5 w-3.5" /> Ask</Button>
              </div>
              {askResult && (
                <div className="selectable mt-3 panel p-3">
                  <p className="whitespace-pre-wrap text-[13.5px] leading-relaxed">{askResult.answer}</p>
                  {askResult.sources.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {askResult.sources.slice(0, 8).map((s) => (
                        <button key={s.segmentId} className="rounded-md border border-border px-2 py-0.5 font-mono text-[11px] text-muted hover:border-accent/50 hover:text-accent" onClick={() => jump(s.start, s.segmentId)}>{fmtTime(s.start)}</button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </section>
          )}
          {d.summary && (
            <section className="mt-7">
              <SectionTitle right={<div className="flex items-center gap-2">
                {d.summary.provider === "extractive" && <span className="text-[11px] text-muted">Built-in notes · set up an AI model under Settings → Models for better summaries</span>}
                <Button size="sm" variant="ghost" loading={refining} title="Correct names, add context or ask for changes — Huddle fixes the transcript and rewrites the notes" onClick={() => { setContextHtml(m.contextHtml ?? ""); setRefine(true); }}><Wand2 className="h-3.5 w-3.5" /> Refine notes</Button>
              </div>}>Summary</SectionTitle>
              <p className="selectable text-[14px] leading-[1.65] text-fg/90">{d.summary.summary || <span className="text-muted">No summary.</span>}</p>
            </section>
          )}

          {d.topics.length > 0 && (
            <section className="mt-7">
              <SectionTitle>Topics</SectionTitle>
              <ul className="selectable flex flex-col gap-1.5">
                {d.topics.map((t) => (
                  <li key={t.id} className="text-[13.5px]"><span className="font-medium">{t.title}</span>{t.summary && <span className="text-fg/70"> — {t.summary}</span>}</li>
                ))}
              </ul>
            </section>
          )}

          {d.decisions.length > 0 && (
            <section className="mt-7">
              <SectionTitle>Decisions</SectionTitle>
              <ul className="selectable flex flex-col gap-1.5">
                {d.decisions.map((x) => (
                  <li key={x.id} className="flex items-start gap-2 text-[13.5px]">
                    <CheckCircle2 className="mt-[3px] h-3.5 w-3.5 shrink-0 text-emerald-600" />
                    <span className="flex-1">{x.text}</span>
                    {x.evidenceStart != null && <button className="font-mono text-[11px] text-muted hover:text-accent" onClick={() => jump(x.evidenceStart, x.segmentId)}>{fmtTime(x.evidenceStart)}</button>}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {(d.actionItems.length > 0 || d.summary) && (
            <section className="mt-7">
              <SectionTitle right={<div className="flex items-center gap-1">
                <Button size="sm" variant="ghost" loading={generating || extracting} onClick={generateItems}><Sparkles className="h-3.5 w-3.5" /> {extracting ? "Reading the transcript…" : d.actionItems.length ? "Find again" : "Find action items"}</Button>
                <Button size="sm" variant="ghost" onClick={() => setEditingItem("new")}><Plus className="h-3.5 w-3.5" /> Add</Button>
              </div>}>Action items</SectionTitle>
              {editingItem === "new" && <ActionItemEditor speakers={d.speakers} onCancel={() => setEditingItem(null)} onSave={(b) => saveItem(null, b)} />}
              {d.actionItems.length === 0 && editingItem !== "new" ? <div className="text-[13px] text-muted">{generating || extracting ? "Looking for commitments in the transcript… items appear here as they are found." : "No action items yet. Use “Find action items” to let the AI extract them, or add one yourself."}</div> : (
                <ul className="flex flex-col gap-1">
                  {d.actionItems.map((a) => editingItem === a.id ? (
                    <li key={a.id}><ActionItemEditor item={a} speakers={d.speakers} onCancel={() => setEditingItem(null)} onSave={(b) => saveItem(a, b)} /></li>
                  ) : (
                    <li key={a.id} className="group flex items-start gap-2.5 rounded-md px-1 py-1 hover:bg-fg/[0.03]">
                      <button onClick={() => toggleItem(a)} className="mt-[2px] text-muted hover:text-accent">
                        {a.done ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <Circle className="h-4 w-4" />}
                      </button>
                      <div className="min-w-0 flex-1 selectable">
                        <span className={cn("text-[13.5px]", a.done && "text-muted line-through")}>{a.text}</span>
                        <div className="flex items-center gap-3 text-[12px] text-muted">
                          <span className={cn("inline-flex items-center gap-1", !a.owner && "italic")}><User className="h-3 w-3" />{a.owner ?? "Unassigned"}</span>
                          {a.dueDate && <span className="inline-flex items-center gap-1"><CalendarClock className="h-3 w-3" />{fmtDueDate(a.dueDate)}</span>}
                          {a.confidence != null && a.confidence < 0.6 && <Badge tone="warn">uncertain</Badge>}
                        </div>
                      </div>
                      <div className="flex items-center gap-0.5 opacity-50 transition-opacity group-hover:opacity-100">
                        <Button size="sm" variant="ghost" title="Edit" onClick={() => setEditingItem(a.id)}><Pencil className="h-3.5 w-3.5" /></Button>
                        <Button size="sm" variant="ghost" title="Delete" onClick={() => deleteItem(a)}><Trash2 className="h-3.5 w-3.5 text-danger" /></Button>
                      </div>
                      {a.evidenceStart != null && <button className="mt-[3px] font-mono text-[11px] text-muted hover:text-accent" onClick={() => jump(a.evidenceStart, a.segmentId)}>{fmtTime(a.evidenceStart)}</button>}
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}

          {d.segments.length > 0 && (
            <section className="mt-7">
              <SectionTitle right={<span className="text-[11px] text-muted">Click a speaker to rename · click text to seek</span>}>Transcript</SectionTitle>
              <TranscriptView segments={d.segments} speakers={d.speakers} currentTime={time} activeSegmentId={activeSeg} onSeek={seekTo} onSpeakerClick={setRenaming} />
            </section>
          )}

        </div>
      </div>

      <RenameDialog speaker={renaming} speakers={d.speakers} onClose={() => setRenaming(null)}
        onRename={async (name, enroll) => { if (renaming) { await api.renameSpeaker(id, renaming.id, name, enroll); setRenaming(null); load(); onChanged(); } }}
        onMerge={async (targetId) => { if (renaming) { await api.mergeSpeakers(id, renaming.id, targetId); setRenaming(null); load(); } }} />

      {actions.dialogs}

      <Dialog open={refine} onClose={() => setRefine(false)} title="Refine notes" width={600}
        footer={<><Button variant="ghost" onClick={() => setRefine(false)}>Cancel</Button><Button variant="primary" disabled={!contextHtml.replace(/<[^>]*>/g, "").trim()} onClick={applyRefine}><Wand2 className="h-3.5 w-3.5" /> Apply</Button></>}>
        <p className="mb-3 text-muted">Tell Huddle what it got wrong or what it should know: who a speaker is, how a project or product is really called, a decision it missed or invented. Names and words are corrected in the transcript; the summary, topics, decisions and action items are rewritten with your notes as the authority. Your notes stay with the meeting and are used again on every later rewrite.</p>
        <RichTextEditor autoFocus value={contextHtml} onChange={setContextHtml}
          placeholder="For example: We did not decide on the launch date yet…" />
      </Dialog>
    </div>
  );
}

function ActionItemEditor({ item, speakers, onSave, onCancel }: { item?: ActionItem; speakers: MeetingSpeaker[]; onSave: (b: { text: string; owner: string | null; dueDate: string | null }) => void; onCancel: () => void }) {
  const [text, setText] = useState(item?.text ?? "");
  const [owner, setOwner] = useState(item?.owner ?? "");
  const [due, setDue] = useState(item?.dueDate ?? "");
  const names = Array.from(new Set(speakers.map((s) => speakerDisplay(s)).filter((n) => !/^Speaker \d+$/.test(n))));
  return (
    <div className="mb-2 panel p-3">
      <Input autoFocus placeholder="What needs to be done?" value={text} onChange={(e) => setText(e.target.value)} />
      <div className="mt-2 flex items-center gap-2">
        <Input list="owner-names" placeholder="Owner (optional)" value={owner} onChange={(e) => setOwner(e.target.value)} className="w-[200px]" />
        <datalist id="owner-names">{names.map((n) => <option key={n} value={n} />)}</datalist>
        <Input type="date" value={due} onChange={(e) => setDue(e.target.value)} className="w-[160px]" />
        <div className="flex-1" />
        <Button size="sm" variant="ghost" onClick={onCancel}><X className="h-3.5 w-3.5" /></Button>
        <Button size="sm" variant="primary" disabled={!text.trim()} onClick={() => onSave({ text: text.trim(), owner: owner.trim() || null, dueDate: due || null })}><Check className="h-3.5 w-3.5" /> Save</Button>
      </div>
    </div>
  );
}

function RenameDialog({ speaker, speakers, onClose, onRename, onMerge }: { speaker: MeetingSpeaker | null; speakers: MeetingSpeaker[]; onClose: () => void; onRename: (name: string, enroll: boolean) => Promise<void>; onMerge: (targetId: number) => Promise<void> }) {
  const [name, setName] = useState("");
  const [enroll, setEnroll] = useState(true);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setName(speaker ? speakerDisplay(speaker) === speaker.label ? "" : speakerDisplay(speaker) : ""); setEnroll(true); }, [speaker]);
  if (!speaker) return null;
  const color = speakerColor(speaker.colorIndex);
  const others = speakers.filter((s) => s.id !== speaker.id);
  return (
    <Dialog open onClose={onClose} title={`Rename ${speaker.label}`}
      footer={<><Button onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} disabled={!name.trim()} onClick={async () => { setBusy(true); try { await onRename(name.trim(), enroll); } finally { setBusy(false); } }}>Save</Button></>}>
      <div className="mb-3 flex items-center gap-2 text-[12px] text-muted"><span className={cn("h-2 w-2 rounded-full", color.dot)} />{fmtTalkTime(speaker.talkTimeSec)} of speech in this meeting{speaker.nameSource === "inferred" ? " · currently named from the conversation" : ""}</div>
      <Input autoFocus placeholder="Name" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && name.trim() && onRename(name.trim(), enroll)} />
      <label className="mt-3 flex items-start gap-2 text-[12.5px]">
        <input type="checkbox" className="mt-[3px]" checked={enroll} onChange={(e) => setEnroll(e.target.checked)} />
        <span>Remember this voice so Huddle can recognise <b>{name.trim() || "this person"}</b> in future meetings.</span>
      </label>
      <p className="mt-2 text-[11.5px] text-muted">The name is updated in the summary, decisions and action items of this meeting too.</p>
      {others.length > 0 && (
        <div className="mt-4 border-t border-border pt-3">
          <div className="mb-1.5 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">Or merge into</div>
          <div className="flex flex-wrap gap-1.5">
            {others.map((o) => (
              <button key={o.id} className="rounded-lg border border-border px-2 py-1 text-[12px] hover:border-accent/50 hover:text-accent" onClick={() => onMerge(o.id)}>{speakerDisplay(o)}</button>
            ))}
          </div>
          <div className="mt-1.5 text-[11.5px] text-muted">Use this when the same person was detected as two speakers.</div>
        </div>
      )}
    </Dialog>
  );
}
