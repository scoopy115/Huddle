import { openUrl } from "@tauri-apps/plugin-opener";
import { native } from "@/lib/native";
import { fmtBytes } from "@/lib/format";
import { dismissUpdate, installUpdate, useUpdates } from "@/lib/updates";
import { Button, Dialog, Spinner } from "@/components/ui";

const PHASES: Record<string, string> = { downloading: "Downloading", extracting: "Unpacking", installing: "Moving to Downloads" };

/** "A new version of Huddle is available" — shown over the app whenever a check finds a newer release. */
export function UpdateDialog() {
  const u = useUpdates();
  const info = u.available;
  if (!info) return null;
  const busy = !!u.installing;
  const p = u.installing;
  const pct = p?.total ? Math.min(100, Math.round((p.downloaded / p.total) * 100)) : null;
  const notes = info.notes.trim().replace(/\r/g, "").slice(0, 900);
  const done = u.unpacked;

  return (
    <Dialog open={u.prompt} onClose={() => { if (!busy) dismissUpdate(); }} title={done ? `Huddle ${info.version} is in your Downloads` : "A new version of Huddle is available"}
      footer={busy ? null : done ? (
        <>
          <Button variant="ghost" onClick={dismissUpdate}>Done</Button>
          <Button variant="primary" onClick={() => native.revealInFinder(done.appPath)}>Show in Finder</Button>
        </>
      ) : (
        <>
          <Button variant="ghost" onClick={dismissUpdate}>Cancel</Button>
          {info.assetUrl ? (
            <Button variant="primary" onClick={installUpdate}>Download</Button>
          ) : (
            <Button variant="primary" onClick={() => openUrl(info.pageUrl)}>Open download page</Button>
          )}
        </>
      )}>
      {done ? (
        <p className="text-muted">
          The new version was unpacked to <span className="font-mono text-[12px] text-fg/80">{done.folder}</span>. Quit Huddle, then drag the new Huddle.app into your Applications folder, replacing the old one.
        </p>
      ) : (
        <p className="text-muted">
          Huddle {info.version} is ready to download{u.currentVersion ? `; you have ${u.currentVersion}` : ""}.
          {info.assetSize ? ` The download is ${fmtBytes(info.assetSize)}.` : ""} It is unpacked into your Downloads folder; you then move it to Applications yourself.
        </p>
      )}
      {notes && !busy && !done && !u.installError && (
        <pre className="mt-3 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-md bg-fg/[0.04] p-2.5 font-sans text-[12px] leading-relaxed text-fg/80">{notes}</pre>
      )}
      {p && (
        <div className="mt-4">
          <div className="flex items-center justify-between text-[12px] text-muted">
            <span className="flex items-center gap-2"><Spinner className="h-3.5 w-3.5" /> {PHASES[p.phase] ?? p.phase}…</span>
            {p.phase === "downloading" && <span>{fmtBytes(p.downloaded)}{p.total ? ` of ${fmtBytes(p.total)}` : ""}</span>}
          </div>
          <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-fg/10">
            <div className={pct === null || p.phase !== "downloading" ? "h-full w-full animate-pulse rounded-full bg-ink/60" : "h-full rounded-full bg-ink transition-[width]"} style={pct !== null && p.phase === "downloading" ? { width: `${pct}%` } : undefined} />
          </div>
        </div>
      )}
      {u.installError && <p className="mt-3 text-[12.5px] text-danger">{u.installError}</p>}
    </Dialog>
  );
}
