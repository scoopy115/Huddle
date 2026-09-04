import { useEffect, useState, type ReactNode } from "react";
import { Download, FileAudio, Languages, RotateCw, Sparkles, Trash2 } from "lucide-react";
import { save } from "@tauri-apps/plugin-dialog";
import { api, errorMessage } from "@/lib/api";
import { native } from "@/lib/native";
import { languageOptions } from "@/lib/languages";
import { cn } from "@/lib/utils";
import { Button, DangerDialog, Dialog, Select } from "@/components/ui";

/** The subset of a meeting the actions need — satisfied by both the list item and the detail. */
export interface MenuMeeting {
  id: string;
  title: string;
  language: string | null;
  languageOverride: string | null;
  speakerCountHint?: number | null;
}

/** Options for the "how many people spoke" hint: 0 = let the diarizer decide. */
export const SPEAKER_COUNT_OPTIONS = [0, 1, 2, 3, 4, 5, 6, 7, 8];
export const speakerCountLabel = (n: number) => (n === 0 ? "Detect automatically" : n === 1 ? "1 person" : `${n} people`);

export type MeetingAction = "export-md" | "export-txt" | "export-json" | "export-srt" | "export-audio" | "language" | "summary" | "reprocess" | "delete";

/**
 * Everything the "…" menu on a meeting can do — shared by the detail page and the
 * right-click menu in the overview. `run` starts an action for a meeting; dialogs that
 * need confirmation keep their own copy of the meeting, so the caller may drop it.
 */
export function useMeetingActions({ onChanged, onDeleted }: { onChanged: (m: MenuMeeting) => void; onDeleted?: (m: MenuMeeting) => void }) {
  const [target, setTarget] = useState<MenuMeeting | null>(null);
  const [dialog, setDialog] = useState<"language" | "reprocess" | "delete" | null>(null);
  const [langChoice, setLangChoice] = useState("");
  const [countChoice, setCountChoice] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const exportAs = async (m: MenuMeeting, format: "md" | "txt" | "json" | "srt") => {
    const body = await api.exportMeeting(m.id, format);
    const safe = m.title.replace(/[^\w\s-]/g, "").trim().replace(/\s+/g, "-").slice(0, 60) || "meeting";
    const path = await save({ defaultPath: `${safe}.${format}`, filters: [{ name: format.toUpperCase(), extensions: [format] }] });
    if (path) await native.saveTextFile(path, typeof body === "string" ? body : JSON.stringify(body, null, 2));
  };

  const run = async (action: MeetingAction, m: MenuMeeting) => {
    setError(null);
    setTarget(m);
    try {
      switch (action) {
        case "export-md": case "export-txt": case "export-json": case "export-srt":
          await exportAs(m, action.slice(7) as "md" | "txt" | "json" | "srt");
          break;
        case "export-audio": {
          const detail = await api.getMeeting(m.id);
          if (!detail.audioPath) throw new Error("This meeting has no audio file any more.");
          const ext = detail.audioPath.split(".").pop() || "wav";
          const safe = m.title.replace(/[^\w\s-]/g, "").trim().replace(/\s+/g, "-").slice(0, 60) || "meeting";
          const path = await save({ defaultPath: `${safe}.${ext}`, filters: [{ name: "Audio", extensions: [ext] }] });
          if (path) await native.copyFile(detail.audioPath, path);
          break;
        }
        case "language":
          setLangChoice(m.languageOverride ?? (m.language ?? "").split(",")[0] ?? "");
          setDialog("language");
          break;
        case "summary":
          await api.retryStage(m.id, "summarizing");
          onChanged(m);
          break;
        case "reprocess":
          setLangChoice(m.languageOverride ?? "");
          setCountChoice(m.speakerCountHint ?? 0);
          setDialog("reprocess");
          break;
        case "delete":
          setDialog("delete");
          break;
      }
    } catch (e) { setError(errorMessage(e)); }
  };

  const close = () => setDialog(null);
  const langSelect = (
    <Select wide className="mt-1" value={langChoice} onChange={(e) => setLangChoice(e.target.value)}>
      <option value="">Detect automatically</option>
      {languageOptions().map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
    </Select>
  );

  const dialogs: ReactNode = target && (
    <>
      <Dialog open={dialog === "reprocess"} onClose={close} title="Reprocess meeting"
        footer={<><Button variant="ghost" onClick={close}>Cancel</Button><Button variant="primary" onClick={async () => { close(); try { await api.process(target.id, { languageOverride: langChoice, speakerCount: countChoice }); onChanged(target); } catch (e) { setError(errorMessage(e)); } }}>Start</Button></>}>
        <p className="mb-3 text-muted">Transcript, speakers and notes are generated again. The current version stays until each step has finished, so you can cancel at any time and keep what you have.</p>
        <label className="block text-[12px] text-muted">Spoken language</label>
        {langSelect}
        <label className="mt-3 block text-[12px] text-muted">How many people spoke?</label>
        <Select wide className="mt-1" value={String(countChoice)} onChange={(e) => setCountChoice(Number(e.target.value))}>
          {SPEAKER_COUNT_OPTIONS.map((n) => <option key={n} value={n}>{speakerCountLabel(n)}</option>)}
        </Select>
        <p className="mt-1 text-[11.5px] text-muted">Telling Huddle the number of people makes speaker separation noticeably more reliable.</p>
      </Dialog>

      <Dialog open={dialog === "language"} onClose={close} title="Spoken language"
        footer={<><Button variant="ghost" onClick={close}>Cancel</Button><Button variant="primary" onClick={async () => { close(); try { await api.updateMeeting(target.id, { languageOverride: langChoice }); onChanged(target); } catch (e) { setError(errorMessage(e)); } }}>Re-transcribe</Button></>}>
        <p className="mb-2 text-muted">If the language was detected wrongly, choose the right one. The whole meeting is transcribed again in that language and the notes are regenerated.</p>
        {langSelect}
      </Dialog>

      <DangerDialog open={dialog === "delete"} onClose={close} title="Delete this meeting?" confirmLabel="Delete meeting" seconds={0}
        onConfirm={async () => { await api.deleteMeeting(target.id); close(); onDeleted?.(target); onChanged(target); }}>
        The recording, transcript, summary and action items of “{target.title}” will be permanently removed from this Mac.
      </DangerDialog>
    </>
  );

  return { run, dialogs, error };
}

const ITEM = "flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12.5px] hover:bg-fg/[0.05]";

/** The rows of the meeting menu; the caller decides where the popover sits. */
export function MeetingMenuList({ onPick }: { onPick: (a: MeetingAction) => void }) {
  return (
    <>
      {(["md", "txt", "json", "srt"] as const).map((f) => (
        <button key={f} className={ITEM} onClick={() => onPick(`export-${f}` as MeetingAction)}>
          <Download className="h-3.5 w-3.5 text-muted" /> Export {f === "md" ? "Markdown" : f.toUpperCase()}
        </button>
      ))}
      <button className={ITEM} onClick={() => onPick("export-audio")}><FileAudio className="h-3.5 w-3.5 text-muted" /> Export audio…</button>
      <div className="my-1 border-t border-border" />
      <button className={ITEM} onClick={() => onPick("language")}><Languages className="h-3.5 w-3.5 text-muted" /> Change spoken language…</button>
      <button className={ITEM} onClick={() => onPick("summary")}><Sparkles className="h-3.5 w-3.5 text-muted" /> Regenerate summary</button>
      <button className={ITEM} onClick={() => onPick("reprocess")}><RotateCw className="h-3.5 w-3.5 text-muted" /> Reprocess meeting…</button>
      <div className="my-1 border-t border-border" />
      <button className={cn(ITEM, "text-danger hover:bg-danger/10")} onClick={() => onPick("delete")}><Trash2 className="h-3.5 w-3.5" /> Delete meeting</button>
    </>
  );
}

/** Right-click menu at a screen position; closes on outside click, Escape or scroll. */
export function MeetingContextMenu({ position, onClose, onPick }: { position: { x: number; y: number }; onClose: () => void; onPick: (a: MeetingAction) => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    window.addEventListener("scroll", onClose, true);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("scroll", onClose, true); };
  }, [onClose]);
  const W = 220, H = 300;
  const x = Math.min(position.x, window.innerWidth - W - 8);
  const y = Math.min(position.y, window.innerHeight - H - 8);
  return (
    <div className="fixed inset-0 z-40" onMouseDown={onClose} onContextMenu={(e) => { e.preventDefault(); onClose(); }}>
      <div className="animate-rise panel absolute p-1 shadow-xl" style={{ left: x, top: y, width: W }} onMouseDown={(e) => e.stopPropagation()}>
        <MeetingMenuList onPick={(a) => { onClose(); onPick(a); }} />
      </div>
    </div>
  );
}
