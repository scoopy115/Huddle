import { useEffect, useState } from "react";
import { CalendarClock, Check, CheckCircle2, CheckSquare, Circle, Headphones, Pencil, Trash2, User, X } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { ActionItem } from "@/types/engine";
import { fmtDate, fmtDueDate } from "@/lib/format";
import { useNav } from "@/lib/nav";
import { cn } from "@/lib/utils";
import { Button, EmptyState, Input, Spinner } from "@/components/ui";

export function ActionItemsScreen({ onChanged }: { onChanged: () => void }) {
  const { go } = useNav();
  const [items, setItems] = useState<ActionItem[] | null>(null);
  const [showDone, setShowDone] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => api.actionItems(false).then(setItems).catch((e) => setError(errorMessage(e)));
  useEffect(() => { load(); }, []);

  const toggle = async (a: ActionItem) => {
    setItems((list) => list?.map((x) => (x.id === a.id ? { ...x, done: !x.done } : x)) ?? null);
    try { await api.updateActionItem(a.id, { done: !a.done }); onChanged(); } catch (e) { setError(errorMessage(e)); load(); }
  };
  const remove = async (a: ActionItem) => {
    setItems((list) => list?.filter((x) => x.id !== a.id) ?? null);
    try { await api.deleteActionItem(a.id); onChanged(); } catch (e) { setError(errorMessage(e)); load(); }
  };
  const save = async (a: ActionItem, body: { text: string; owner: string | null; dueDate: string | null }) => {
    try { await api.updateActionItem(a.id, body); setEditing(null); load(); } catch (e) { setError(errorMessage(e)); }
  };

  const visible = (items ?? []).filter((a) => showDone || !a.done);
  const groups = visible.reduce<Record<string, ActionItem[]>>((acc, a) => { (acc[a.meetingId] ??= []).push(a); return acc; }, {});

  return (
    <div className="flex h-full flex-col">
      <header data-tauri-drag-region className="titlebar-drag flex h-[52px] shrink-0 items-center gap-3 border-b border-border px-5">
        <h1 data-tauri-drag-region className="page-title">Action Items</h1>
        <div data-tauri-drag-region className="flex-1" />
        <Button variant="ghost" size="sm" onClick={() => setShowDone(!showDone)}>{showDone ? "Hide completed" : "Show completed"}</Button>
      </header>
      <div className="mx-auto w-full max-w-[860px] flex-1 overflow-y-auto px-5 py-4">
        {error && <div className="mb-3 text-[12.5px] text-danger">{error}</div>}
        {!items ? <div className="flex justify-center py-8"><Spinner /></div> : visible.length === 0 ? (
          <EmptyState icon={<CheckSquare className="h-7 w-7" />} title={showDone ? "No action items" : "Nothing open"} body="Action items from your meetings appear here with their owner and due date when one was mentioned." />
        ) : (
          Object.entries(groups).map(([mid, list]) => (
            <section key={mid} className="mb-5">
              <button className="mb-1.5 flex items-baseline gap-2 px-1 text-left" onClick={() => go({ kind: "meeting", id: mid })}>
                <span className="text-[13.5px] font-semibold">{list[0].meetingTitle}</span>
                <span className="text-[12px] text-muted">{fmtDate(list[0].meetingStartedAt ?? null)}</span>
              </button>
              <div className="panel overflow-hidden">
                {list.map((a) => editing === a.id ? (
                  <Editor key={a.id} item={a} onCancel={() => setEditing(null)} onSave={(b) => save(a, b)} />
                ) : (
                  <div key={a.id} className="group flex items-start gap-3 border-b border-border px-4 py-2.5 last:border-b-0">
                    <button onClick={() => toggle(a)} className="mt-[2px] text-muted hover:text-accent">
                      {a.done ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <Circle className="h-4 w-4" />}
                    </button>
                    <div className="min-w-0 flex-1">
                      <div className={cn("text-[13.5px]", a.done && "text-muted line-through")}>{a.text}</div>
                      <div className="mt-0.5 flex items-center gap-3 text-[12px] text-muted">
                        <span className={cn("inline-flex items-center gap-1", !a.owner && "italic")}><User className="h-3 w-3" />{a.owner ?? "Unassigned"}</span>
                        {a.dueDate && <span className="inline-flex items-center gap-1"><CalendarClock className="h-3 w-3" />{fmtDueDate(a.dueDate)}</span>}
                        {a.evidenceStart != null && (
                          <button className="inline-flex items-center gap-1 hover:text-accent" onClick={() => go({ kind: "meeting", id: a.meetingId, seek: a.evidenceStart!, segmentId: a.segmentId ?? undefined })}><Headphones className="h-3 w-3" /> Listen</button>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-0.5 opacity-50 transition-opacity group-hover:opacity-100">
                      <Button size="sm" variant="ghost" title="Edit" onClick={() => setEditing(a.id)}><Pencil className="h-3.5 w-3.5" /></Button>
                      <Button size="sm" variant="ghost" title="Delete" onClick={() => remove(a)}><Trash2 className="h-3.5 w-3.5 text-danger" /></Button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ))
        )}
      </div>
    </div>
  );
}

function Editor({ item, onSave, onCancel }: { item: ActionItem; onSave: (b: { text: string; owner: string | null; dueDate: string | null }) => void; onCancel: () => void }) {
  const [text, setText] = useState(item.text);
  const [owner, setOwner] = useState(item.owner ?? "");
  const [due, setDue] = useState(item.dueDate ?? "");
  return (
    <div className="border-b border-border bg-fg/[0.02] px-4 py-3 last:border-b-0">
      <Input autoFocus value={text} onChange={(e) => setText(e.target.value)} />
      <div className="mt-2 flex items-center gap-2">
        <Input placeholder="Owner (optional)" value={owner} onChange={(e) => setOwner(e.target.value)} className="w-[200px]" />
        <Input type="date" value={due} onChange={(e) => setDue(e.target.value)} className="w-[160px]" />
        <div className="flex-1" />
        <Button size="sm" variant="ghost" onClick={onCancel}><X className="h-3.5 w-3.5" /></Button>
        <Button size="sm" variant="primary" disabled={!text.trim()} onClick={() => onSave({ text: text.trim(), owner: owner.trim() || null, dueDate: due || null })}><Check className="h-3.5 w-3.5" /> Save</Button>
      </div>
    </div>
  );
}
