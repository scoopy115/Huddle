import { useEffect, useState } from "react";
import { Activity, Download, Mic, X } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { ProcessesInfo } from "@/types/engine";
import { fmtBytes, fmtEta, fmtTime } from "@/lib/format";
import { useNav } from "@/lib/nav";
import { Button, EmptyState, Spinner } from "@/components/ui";

const STAGE_WORD: Record<string, string> = {
  preprocessing: "Preparing audio", transcribing: "Transcribing", diarizing: "Detecting speakers",
  identifying_speakers: "Recognising voices", refining: "Applying feedback", extracting_actions: "Finding action items", summarizing: "Generating summary", indexing: "Indexing",
};

export function ProcessesScreen({ onChanged }: { onChanged: () => void }) {
  const { go } = useNav();
  const [info, setInfo] = useState<ProcessesInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const tick = () => api.processes().then(setInfo).catch((e) => setError(errorMessage(e)));
    tick();
    const t = setInterval(tick, 1500);
    return () => clearInterval(t);
  }, []);

  const total = (info?.jobs.length ?? 0) + (info?.live.length ?? 0) + (info?.downloads.length ?? 0);

  return (
    <div className="flex h-full flex-col">
      <header data-tauri-drag-region className="titlebar-drag flex h-[52px] shrink-0 items-center gap-3 border-b border-border px-5">
        <h1 data-tauri-drag-region className="page-title">Processes</h1>
        <div data-tauri-drag-region className="flex-1" />
      </header>
      <div className="mx-auto w-full max-w-[860px] flex-1 overflow-y-auto px-5 py-4">
        {error && <div className="mb-3 text-[12.5px] text-danger">{error}</div>}
        {!info ? <div className="flex justify-center py-8"><Spinner /></div> : total === 0 ? (
          <EmptyState icon={<Activity className="h-7 w-7" />} title="Nothing running" body="Transcriptions, speaker detection, summaries and downloads show up here while they run." />
        ) : (
          <>
            {info.jobs.length > 0 && (
              <section className="mb-5">
                <div className="mb-1.5 px-2 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">Meetings</div>
                <div className="panel overflow-hidden">
                  {info.jobs.map((j) => {
                    const pct = j.progress != null ? Math.round(j.progress * 100) : null;
                    const eta = fmtEta(j.startedAt, j.progress);
                    return (
                      <div key={j.meetingId} className="flex items-center gap-4 border-b border-border px-4 py-3 last:border-b-0">
                        <Spinner className="h-4 w-4 text-accent" />
                        <div className="min-w-0 flex-1">
                          <button className="truncate text-[13.5px] font-medium hover:text-accent" onClick={() => go({ kind: "meeting", id: j.meetingId })}>{j.title}</button>
                          <div className="mt-0.5 flex items-center gap-2 text-[12px] text-muted">
                            <span>{j.state === "queued" ? "Waiting for another meeting to finish" : (j.stage ? STAGE_WORD[j.stage] ?? j.stage : "Starting")}</span>
                            {pct != null && <span className="font-mono tabular-nums text-accent">{pct}%</span>}
                            {eta && <span>{eta}</span>}
                          </div>
                          {pct != null && <div className="mt-1.5 h-1 w-full max-w-[320px] overflow-hidden rounded bg-fg/10"><div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} /></div>}
                        </div>
                        <Button size="sm" variant="ghost" title="Cancel — keeps the previous version" onClick={async () => { await api.cancelProcessing(j.meetingId); onChanged(); }}><X className="h-3.5 w-3.5" /> Cancel</Button>
                      </div>
                    );
                  })}
                </div>
              </section>
            )}
            {info.live.length > 0 && (
              <section className="mb-5">
                <div className="mb-1.5 px-2 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">Live transcription</div>
                <div className="panel overflow-hidden">
                  {info.live.map((l) => (
                    <div key={l.recordingId} className="flex items-center gap-4 px-4 py-3">
                      <Mic className="h-4 w-4 text-record" />
                      <div className="min-w-0 flex-1">
                        <div className="text-[13.5px] font-medium">Recording in progress</div>
                        <div className="text-[12px] text-muted">{fmtTime(l.processedSec)} transcribed · {l.segmentCount} segments</div>
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}
            {info.downloads.length > 0 && (
              <section className="mb-5">
                <div className="mb-1.5 px-2 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">Downloads</div>
                <div className="panel overflow-hidden">
                  {info.downloads.map((d) => {
                    const pct = d.totalBytes ? Math.round((d.receivedBytes / d.totalBytes) * 100) : 0;
                    return (
                      <div key={d.id} className="flex items-center gap-4 border-b border-border px-4 py-3 last:border-b-0">
                        <Download className="h-4 w-4 text-muted" />
                        <div className="min-w-0 flex-1">
                          <div className="text-[13.5px] font-medium">{d.candidate.name}</div>
                          <div className="text-[12px] text-muted">{fmtBytes(d.receivedBytes)} of {fmtBytes(d.totalBytes)} · {pct}%</div>
                          <div className="mt-1.5 h-1 w-full max-w-[320px] overflow-hidden rounded bg-fg/10"><div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} /></div>
                        </div>
                        <Button size="sm" variant="ghost" onClick={() => api.cancelDownload(d.id)}><X className="h-3.5 w-3.5" /> Cancel</Button>
                      </div>
                    );
                  })}
                </div>
              </section>
            )}
          </>
        )}
      </div>
    </div>
  );
}
