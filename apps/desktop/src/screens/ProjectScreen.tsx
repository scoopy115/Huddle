import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, Check, CheckSquare, Folder, MessageSquareText, Mic, MoreHorizontal, Pencil, Plus, Sparkles, Trash2, X } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { AskResult, Meeting, ProjectDetail } from "@/types/engine";
import { fmtRelativeDay, fmtTime } from "@/lib/format";
import { AI_MISSING_HINT, useNav } from "@/lib/nav";
import { cn, speakerColor } from "@/lib/utils";
import { Button, DangerDialog, Dialog, Input, SectionTitle, Spinner } from "@/components/ui";
import { MeetingContextMenu, useMeetingActions } from "@/components/MeetingMenu";
import { MeetingGroups, MeetingRow } from "@/components/MeetingList";
import { ProjectForm } from "@/screens/ProjectsScreen";

export function ProjectScreen({ id, onChanged }: { id: string; onChanged: () => void }) {
  const { go, ai } = useNav();
  const [d, setD] = useState<ProjectDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [menu, setMenu] = useState(false);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [adding, setAdding] = useState(false);
  const [ctx, setCtx] = useState<{ m: Meeting; x: number; y: number } | null>(null);
  const [ask, setAsk] = useState("");
  const [askResult, setAskResult] = useState<AskResult | null>(null);
  const [asking, setAsking] = useState(false);

  const load = useCallback(async () => {
    try { setD(await api.getProject(id)); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, [id]);
  useEffect(() => { setD(null); load(); }, [load]);
  const changed = () => { load(); onChanged(); };
  const actions = useMeetingActions({ onChanged: changed });

  // Keep the list fresh while a meeting in it is still processing.
  const processing = d?.meetings.some((m) => m.status === "processing") ?? false;
  useEffect(() => {
    if (!processing) return;
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [processing, load]);

  const decide = async (m: Meeting, accept: boolean) => {
    try {
      if (accept) await api.setMeetingProject(m.id, id);
      else await api.dismissProjectSuggestion(m.id);
      changed();
    } catch (e) { setError(errorMessage(e)); }
  };
  const doAsk = async () => {
    if (!ask.trim()) return;
    setAsking(true);
    try { setAskResult(await api.askAll(ask, id)); } catch (e) { setError(errorMessage(e)); } finally { setAsking(false); }
  };

  if (error && !d) return <div className="p-8 text-[13px] text-danger">{error}</div>;
  if (!d) return <div className="flex h-full items-center justify-center"><Spinner /></div>;
  const p = d.project;
  const c = speakerColor(p.colorIndex);

  return (
    <div className="flex h-full flex-col">
      <header data-tauri-drag-region className="titlebar-drag flex h-[52px] shrink-0 items-center gap-2 border-b border-border px-4">
        <Button variant="ghost" size="sm" onClick={() => go({ kind: "projects" })}><ArrowLeft className="h-4 w-4" /></Button>
        <div data-tauri-drag-region className="flex-1" />
        <Button variant="secondary" size="sm" onClick={() => setAdding(true)}><Plus className="h-3.5 w-3.5" /> Add meetings</Button>
        <div className="relative">
          <Button variant="ghost" size="sm" onClick={() => setMenu(!menu)}><MoreHorizontal className="h-4 w-4" /></Button>
          {menu && (
            <div className="animate-rise absolute right-0 top-8 z-20 w-[200px] panel p-1 shadow-xl" onMouseLeave={() => setMenu(false)}>
              <button className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12.5px] hover:bg-fg/[0.05]" onClick={() => { setMenu(false); setEditing(true); }}><Pencil className="h-3.5 w-3.5 text-muted" /> Edit name and description…</button>
              <div className="my-1 border-t border-border" />
              <button className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12.5px] text-danger hover:bg-danger/10" onClick={() => { setMenu(false); setDeleting(true); }}><Trash2 className="h-3.5 w-3.5" /> Delete project</button>
            </div>
          )}
        </div>
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[860px] px-5 py-6">
          <div className="mb-5 flex items-start gap-4">
            <span className={cn("mt-0.5 flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl", c.bg, c.text)}><Folder className="h-5 w-5" /></span>
            <div className="min-w-0 flex-1">
              <h1 className="selectable cursor-text font-display text-[26px] font-bold leading-tight tracking-tight" onDoubleClick={() => setEditing(true)} title="Double-click to edit">{p.name}</h1>
              <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12.5px] text-muted">
                <span className="inline-flex items-center gap-1.5"><Mic className="h-3.5 w-3.5" />{p.meetingCount} meeting{p.meetingCount === 1 ? "" : "s"}</span>
                {p.openActionCount ? <button className="inline-flex items-center gap-1.5 text-accent hover:underline" onClick={() => go({ kind: "actions" })}><CheckSquare className="h-3.5 w-3.5" />{p.openActionCount} open action item{p.openActionCount === 1 ? "" : "s"}</button> : null}
                {p.lastMeetingAt ? <span>Last meeting {fmtRelativeDay(p.lastMeetingAt).toLowerCase()}</span> : null}
              </div>
              {p.description ? <p className="selectable mt-2 text-[13.5px] leading-relaxed text-fg/80">{p.description}</p>
                : <button className="mt-2 text-[12.5px] text-muted hover:text-fg" onClick={() => setEditing(true)}>Add a description so Huddle recognises this project's meetings more easily</button>}
            </div>
          </div>

          {error && <div className="mb-4 rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-[12.5px] text-danger">{error}</div>}

          {d.meetings.length > 0 && (
            <section className="mb-6">
              <SectionTitle>Ask this project</SectionTitle>
              <div className="flex gap-2">
                <Input placeholder={ai.ready ? "What is still open for this project?" : AI_MISSING_HINT} value={ask} onChange={(e) => setAsk(e.target.value)} onKeyDown={(e) => e.key === "Enter" && ai.ready && doAsk()} disabled={!ai.ready} />
                <Button variant="primary" loading={asking} onClick={doAsk} disabled={!ai.ready} title={ai.ready ? undefined : AI_MISSING_HINT}><MessageSquareText className="h-3.5 w-3.5" /> Ask</Button>
              </div>
              {askResult && (
                <div className="selectable mt-3 panel p-3">
                  <p className="whitespace-pre-wrap text-[13.5px] leading-relaxed">{askResult.answer}</p>
                  {askResult.sources.length > 0 && (
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {askResult.sources.slice(0, 8).map((s) => (
                        <button key={s.segmentId} className="rounded-md border border-border px-2 py-0.5 text-[11px] text-muted hover:border-accent/50 hover:text-accent" title={s.meetingTitle}
                          onClick={() => go({ kind: "meeting", id: s.meetingId, seek: s.start, segmentId: s.segmentId })}>{s.meetingTitle} · <span className="font-mono">{fmtTime(s.start)}</span></button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </section>
          )}

          {d.suggested.length > 0 && (
            <section className="mb-6">
              <SectionTitle right={<span className="text-[11px] text-muted">Huddle thinks these belong here</span>}><span className="inline-flex items-center gap-1.5"><Sparkles className="h-3 w-3 text-accent" /> Suggested</span></SectionTitle>
              <div className="panel overflow-hidden border-accent/30">
                {d.suggested.map((m) => (
                  <MeetingRow key={m.id} m={m} showProject={false} onOpen={() => go({ kind: "meeting", id: m.id })}
                    extra={<div className="flex shrink-0 items-center gap-1">
                      <Button size="sm" variant="primary" onClick={() => decide(m, true)}><Check className="h-3 w-3" /> Add</Button>
                      <Button size="sm" variant="ghost" onClick={() => decide(m, false)}><X className="h-3 w-3" /> Not this one</Button>
                    </div>} />
                ))}
              </div>
            </section>
          )}

          {ctx && <MeetingContextMenu position={{ x: ctx.x, y: ctx.y }} onClose={() => setCtx(null)} onPick={(a) => actions.run(a, ctx.m)} />}
          {actions.dialogs}
          {actions.error && <div className="mb-3 text-[12.5px] text-danger">{actions.error}</div>}
          {d.meetings.length ? (
            <MeetingGroups meetings={d.meetings} activeId={ctx?.m.id} showProject={false} onOpen={(m) => go({ kind: "meeting", id: m.id })}
              onContextMenu={(m, e) => setCtx({ m, x: e.clientX, y: e.clientY })} />
          ) : (
            <div className="panel flex flex-col items-center gap-2 px-6 py-10 text-center">
              <div className="font-display text-[15px] font-bold tracking-tight">No meetings in this project yet</div>
              <p className="max-w-md text-[12.5px] text-muted">Add existing meetings here, or use “Move to project…” on any meeting. New meetings that sound like this project are suggested automatically after they are summarised.</p>
              <Button variant="secondary" size="sm" className="mt-1" onClick={() => setAdding(true)}><Plus className="h-3.5 w-3.5" /> Add meetings</Button>
            </div>
          )}
        </div>
      </div>

      <ProjectForm open={editing} onClose={() => setEditing(false)} initial={{ name: p.name, description: p.description }} title="Edit project" confirm="Save"
        onSave={async (b) => { await api.updateProject(id, { name: b.name, description: b.description }); changed(); }} />
      <AddMeetingsDialog open={adding} onClose={() => setAdding(false)} projectId={id} onAdded={changed} />
      <DangerDialog open={deleting} onClose={() => setDeleting(false)} title="Delete this project?" confirmLabel="Delete project" seconds={0}
        onConfirm={async () => { await api.deleteProject(id); setDeleting(false); onChanged(); go({ kind: "projects" }); }}>
        Only the folder “{p.name}” is removed. Its {p.meetingCount} meeting{p.meetingCount === 1 ? " stays" : "s stay"} in Huddle, just without a project.
      </DangerDialog>
    </div>
  );
}

/** Pick meetings that are in no project yet and file them here. */
function AddMeetingsDialog({ open, onClose, projectId, onAdded }: { open: boolean; onClose: () => void; projectId: string; onAdded: () => void }) {
  const [list, setList] = useState<Meeting[] | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!open) return;
    setList(null); setPicked(new Set()); setFilter("");
    api.listMeetings(undefined, { unassigned: true }).then(setList).catch(() => setList([]));
  }, [open]);
  const shown = (list ?? []).filter((m) => !filter || m.title.toLowerCase().includes(filter.toLowerCase()));
  const toggle = (id: string) => setPicked((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const add = async () => {
    setBusy(true);
    try { for (const id of picked) await api.setMeetingProject(id, projectId); onAdded(); onClose(); } finally { setBusy(false); }
  };
  return (
    <Dialog open={open} onClose={onClose} title="Add meetings" width={520}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} disabled={!picked.size} onClick={add}>Add {picked.size || ""}</Button></>}>
      <p className="mb-2 text-muted">Meetings that are not in a project yet. To move one out of another project, use “Move to project…” on that meeting.</p>
      <Input placeholder="Filter" value={filter} onChange={(e) => setFilter(e.target.value)} />
      <div className="mt-2 max-h-[320px] overflow-y-auto rounded-lg border border-border">
        {!list ? <div className="flex justify-center py-6"><Spinner /></div>
          : !shown.length ? <div className="px-3 py-6 text-center text-[12.5px] text-muted">{list.length ? "No meetings match." : "Every meeting is already in a project."}</div>
          : shown.map((m) => (
            <label key={m.id} className="flex cursor-pointer items-center gap-2.5 border-b border-border px-3 py-2 text-[13px] last:border-b-0 hover:bg-fg/[0.03]">
              <input type="checkbox" checked={picked.has(m.id)} onChange={() => toggle(m.id)} />
              <span className="min-w-0 flex-1 truncate">{m.title}</span>
              <span className="shrink-0 text-[11.5px] text-muted">{fmtRelativeDay(m.startedAt)}</span>
            </label>
          ))}
      </div>
    </Dialog>
  );
}
