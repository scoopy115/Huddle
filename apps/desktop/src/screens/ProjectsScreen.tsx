import { useCallback, useEffect, useState } from "react";
import { CheckSquare, ChevronRight, Folder, FolderPlus, Mic, Sparkles } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { Project } from "@/types/engine";
import { fmtRelativeDay } from "@/lib/format";
import { useNav } from "@/lib/nav";
import { cn, speakerColor } from "@/lib/utils";
import { Badge, Button, Dialog, EmptyState, Input, Spinner } from "@/components/ui";

/** Name + description form, shared by "New project" here and "Edit" on the project page. */
export function ProjectForm({ open, onClose, initial, title, confirm, onSave }: { open: boolean; onClose: () => void; initial?: { name: string; description: string | null }; title: string; confirm: string; onSave: (body: { name: string; description: string }) => Promise<void> }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (open) { setName(initial?.name ?? ""); setDescription(initial?.description ?? ""); setError(null); } }, [open, initial]);
  const save = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try { await onSave({ name: name.trim(), description: description.trim() }); onClose(); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  };
  return (
    <Dialog open={open} onClose={onClose} title={title} width={460}
      footer={<><Button variant="ghost" onClick={onClose}>Cancel</Button><Button variant="primary" loading={busy} disabled={!name.trim()} onClick={save}>{confirm}</Button></>}>
      <label className="block text-[12px] text-muted">Name</label>
      <Input autoFocus className="mt-1" placeholder="Project name" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && save()} />
      <label className="mt-3 block text-[12px] text-muted">What is it about? <span className="text-muted/70">(optional — helps Huddle recognise its meetings)</span></label>
      <textarea className="selectable mt-1 h-20 w-full resize-none rounded-lg border border-border bg-surface px-2.5 py-2 text-[13px] shadow-sm placeholder:text-muted focus:border-accent/60 focus:outline-none focus:ring-2 focus:ring-accent/20"
        placeholder="For example: the website redesign for one client — homepage, pricing, blog." value={description} onChange={(e) => setDescription(e.target.value)} />
      {error && <div className="mt-2 text-[12.5px] text-danger">{error}</div>}
    </Dialog>
  );
}

export function ProjectsScreen() {
  const { go } = useNav();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    try { setProjects(await api.projects()); setError(null); } catch (e) { setError(errorMessage(e)); }
  }, []);
  useEffect(() => { load(); }, [load]);

  return (
    <div className="flex h-full flex-col">
      <header data-tauri-drag-region className="titlebar-drag flex h-[52px] shrink-0 items-center gap-3 border-b border-border px-5">
        <h1 data-tauri-drag-region className="page-title">Projects</h1>
        <div data-tauri-drag-region className="flex-1" />
        <Button variant="primary" onClick={() => setCreating(true)}><FolderPlus className="h-3.5 w-3.5" /> New project</Button>
      </header>
      <ProjectForm open={creating} onClose={() => setCreating(false)} title="New project" confirm="Create"
        onSave={async (b) => { const p = await api.createProject({ name: b.name, description: b.description || null }); await load(); go({ kind: "project", id: p.id }); }} />
      <div className="flex-1 overflow-y-auto">
        {error && <div className="mx-auto max-w-[860px] px-5 pt-3 text-[12.5px] text-danger">{error}</div>}
        {!projects ? (
          <div className="flex h-full items-center justify-center"><Spinner /></div>
        ) : !projects.length ? (
          <EmptyState
            icon={<Folder className="h-6 w-6" />}
            title="No projects yet"
            body="A project is a folder for the meetings of one client, product or team. Once you have one, Huddle suggests it for new meetings that talk about the same things, and MCP clients can ask about the whole project at once."
            action={<Button variant="primary" onClick={() => setCreating(true)}><FolderPlus className="h-4 w-4" /> New project</Button>}
          />
        ) : (
          <div className="mx-auto max-w-[860px] px-5 py-5">
            <div className="panel overflow-hidden">
              {projects.map((p) => {
                const c = speakerColor(p.colorIndex);
                return (
                  <button key={p.id} onClick={() => go({ kind: "project", id: p.id })}
                    className="group flex w-full items-center gap-4 border-b border-border px-4 py-3.5 text-left last:border-b-0 hover:bg-accent-soft/40 dark:hover:bg-fg/[0.03]">
                    <span className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-xl", c.bg, c.text)}><Folder className="h-4.5 w-4.5" /></span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="truncate font-display text-[14.5px] font-bold tracking-tight">{p.name}</span>
                        {p.suggestionCount > 0 && <Badge tone="accent" title="Meetings Huddle thinks belong here"><Sparkles className="h-3 w-3" />{p.suggestionCount} suggested</Badge>}
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-x-3.5 gap-y-0.5 text-[12px] text-muted">
                        <span className="inline-flex items-center gap-1"><Mic className="h-3 w-3" />{p.meetingCount} meeting{p.meetingCount === 1 ? "" : "s"}</span>
                        {p.openActionCount ? <span className="inline-flex items-center gap-1 text-accent"><CheckSquare className="h-3 w-3" />{p.openActionCount} open</span> : null}
                        {p.lastMeetingAt ? <span>Last meeting {fmtRelativeDay(p.lastMeetingAt).toLowerCase()}</span> : null}
                      </div>
                      {p.description && <div className="mt-1 line-clamp-1 text-[12.5px] text-fg/60">{p.description}</div>}
                    </div>
                    <ChevronRight className="h-4 w-4 shrink-0 text-muted/50 transition-transform group-hover:translate-x-0.5 group-hover:text-accent" />
                  </button>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
