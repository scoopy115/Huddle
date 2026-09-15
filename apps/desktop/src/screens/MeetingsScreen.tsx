import { useEffect, useMemo, useState } from "react";
import { FileAudio, Mic, Search as SearchIcon } from "lucide-react";
import type { Meeting, Project } from "@/types/engine";
import { api } from "@/lib/api";
import { useNav } from "@/lib/nav";
import { Button, EmptyState, Input, Select, Spinner } from "@/components/ui";
import { MeetingContextMenu, useMeetingActions } from "@/components/MeetingMenu";
import { MeetingGroups } from "@/components/MeetingList";

/** Project filter values: every meeting, meetings in no project, or one project. */
const ALL = "";
const NONE = "__none__";

export function MeetingsScreen({ meetings, loading, onImport, onChanged }: { meetings: Meeting[] | null; loading: boolean; onImport: () => void; onChanged: () => void }) {
  const { go } = useNav();
  const [filter, setFilter] = useState("");
  const [project, setProject] = useState(ALL);
  const [projects, setProjects] = useState<Project[]>([]);
  const [ctx, setCtx] = useState<{ m: Meeting; x: number; y: number } | null>(null);
  const actions = useMeetingActions({ onChanged });

  useEffect(() => { api.projects().then(setProjects).catch(() => {}); }, [meetings]);
  // A filtered project that was deleted meanwhile falls back to "All projects".
  useEffect(() => { if (project && project !== NONE && !projects.some((p) => p.id === project)) setProject(ALL); }, [projects, project]);

  const list = useMemo(() => {
    const f = filter.toLowerCase();
    return (meetings ?? []).filter((m) =>
      (project === ALL || (project === NONE ? !m.projectId : m.projectId === project))
      && (!f || m.title.toLowerCase().includes(f) || m.participants.some((p) => p.toLowerCase().includes(f)) || (m.projectName ?? "").toLowerCase().includes(f)));
  }, [meetings, filter, project]);

  return (
    <div className="flex h-full flex-col">
      <header data-tauri-drag-region className="titlebar-drag flex h-[52px] shrink-0 items-center gap-3 border-b border-border px-5">
        <h1 data-tauri-drag-region className="page-title">Meetings</h1>
        <div data-tauri-drag-region className="flex-1" />
        {projects.length > 0 && (
          <Select className="w-[180px]" value={project} onChange={(e) => setProject(e.target.value)} title="Show one project">
            <option value={ALL}>All projects</option>
            <option value={NONE}>No project</option>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        )}
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
        ) : !list.length ? (
          <div className="flex h-full items-center justify-center text-[13px] text-muted">No meetings match.</div>
        ) : (
          <div className="mx-auto max-w-[860px] px-5 py-5">
            <MeetingGroups meetings={list} activeId={ctx?.m.id} showProject={project === ALL} onOpen={(m) => go({ kind: "meeting", id: m.id })}
              onContextMenu={(m, e) => setCtx({ m, x: e.clientX, y: e.clientY })} />
          </div>
        )}
      </div>
    </div>
  );
}
