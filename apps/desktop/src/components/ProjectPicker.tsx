import { useEffect, useState } from "react";
import { Check, Folder, FolderPlus, X } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { Project } from "@/types/engine";
import { cn, speakerColor } from "@/lib/utils";
import { Button, Dialog, Input } from "@/components/ui";

/** The folder icon in the project's colour, used wherever a project is named. */
export function ProjectDot({ colorIndex, className }: { colorIndex: number; className?: string }) {
  return <Folder className={cn("h-3.5 w-3.5", speakerColor(colorIndex).text, className)} />;
}

/**
 * "Which project does this meeting belong to?" — the list of projects with the current one
 * ticked, "No project", and a row to create a new project on the spot. Used by the meeting
 * page's project chip and the "Move to project…" menu action.
 */
export function ProjectPicker({ open, onClose, currentId, onPick, title = "Move to project" }: { open: boolean; onClose: () => void; currentId: string | null; onPick: (projectId: string | null) => Promise<void> | void; title?: string }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    setCreating(false); setName(""); setError(null);
    api.projects().then(setProjects).catch((e) => setError(errorMessage(e)));
  }, [open]);

  const pick = async (id: string | null) => {
    setBusy(true);
    try { await onPick(id); onClose(); } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  };
  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      const p = await api.createProject({ name: name.trim() });
      await onPick(p.id);
      onClose();
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  };

  const ROW = "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13px] hover:bg-fg/[0.05] disabled:opacity-50";
  return (
    <Dialog open={open} onClose={onClose} title={title} width={400}>
      {error && <div className="mb-2 text-[12.5px] text-danger">{error}</div>}
      <div className="max-h-[320px] overflow-y-auto -mx-1">
        <button className={ROW} disabled={busy} onClick={() => pick(null)}>
          <Folder className="h-3.5 w-3.5 text-muted" />
          <span className="flex-1 text-muted">No project</span>
          {!currentId && <Check className="h-3.5 w-3.5 text-accent" />}
        </button>
        {projects.map((p) => (
          <button key={p.id} className={ROW} disabled={busy} onClick={() => pick(p.id)}>
            <ProjectDot colorIndex={p.colorIndex} />
            <span className="flex-1 truncate">{p.name}</span>
            <span className="text-[11px] text-muted">{p.meetingCount}</span>
            {currentId === p.id && <Check className="h-3.5 w-3.5 text-accent" />}
          </button>
        ))}
        {creating ? (
          <div className="mt-1 flex items-center gap-2 px-1">
            <Input autoFocus placeholder="Project name" value={name} onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") create(); if (e.key === "Escape") setCreating(false); }} />
            <Button size="sm" variant="ghost" onClick={() => setCreating(false)}><X className="h-3.5 w-3.5" /></Button>
            <Button size="sm" variant="primary" loading={busy} disabled={!name.trim()} onClick={create}><Check className="h-3.5 w-3.5" /> Create</Button>
          </div>
        ) : (
          <button className={cn(ROW, "text-accent")} disabled={busy} onClick={() => setCreating(true)}>
            <FolderPlus className="h-3.5 w-3.5" /> New project…
          </button>
        )}
      </div>
    </Dialog>
  );
}
