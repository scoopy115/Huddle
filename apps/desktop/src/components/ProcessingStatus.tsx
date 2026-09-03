import { useState } from "react";
import { AlertCircle, Check, ChevronDown, Circle, Loader2, RotateCw } from "lucide-react";
import type { ProcessingJob, StageName } from "@/types/engine";
import { STAGES } from "@/types/engine";
import { cn } from "@/lib/utils";
import { fmtEta } from "@/lib/format";
import { Button } from "./ui";

const LABELS: Record<StageName, { doing: string; done: string; retry: string }> = {
  preprocessing: { doing: "Preparing audio", done: "Audio prepared", retry: "Retry from the start" },
  transcribing: { doing: "Transcribing", done: "Transcript generated", retry: "Retry transcription" },
  diarizing: { doing: "Detecting speakers", done: "Speakers detected", retry: "Retry speaker detection" },
  identifying_speakers: { doing: "Recognising known voices", done: "Known voices checked", retry: "Retry recognition" },
  refining: { doing: "Applying your feedback", done: "Feedback applied", retry: "Retry refining" },
  summarizing: { doing: "Generating summary", done: "Summary generated", retry: "Retry summary" },
  extracting_actions: { doing: "Finding action items", done: "Action items found", retry: "Retry action items" },
  indexing: { doing: "Indexing", done: "Indexed", retry: "Retry indexing" },
};

export function ProcessingStatus({ job, onRetry, onCancel }: { job: ProcessingJob; onRetry: (stage: string) => void; onCancel?: () => void }) {
  const [showDetail, setShowDetail] = useState<string | null>(null);
  const running = job.state === "running";
  const queued = job.state === "queued";
  const failed = STAGES.filter((s) => job.stages[s]?.status === "failed");

  return (
    <div className="panel p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-[13px] font-semibold">
          {queued ? "Waiting for another meeting to finish" : running ? "Processing meeting" : failed.length ? "Processing needs attention" : "Processed"}
        </div>
        <div className="flex items-center gap-2">
          {queued && <Loader2 className="h-3.5 w-3.5 animate-spin text-muted" />}
          {(running || queued) && onCancel && <Button size="sm" variant="ghost" onClick={onCancel} title="Cancel — keeps the previous version">Cancel</Button>}
        </div>
      </div>
      <ol className="flex flex-col gap-1.5">
        {STAGES.filter((s) => !(job.stages[s]?.status === "skipped" && !job.stages[s]?.error)).map((s) => {
          const st = job.stages[s]?.status ?? "pending";
          const prog = job.stages[s]?.progress;
          const l = LABELS[s];
          const pct = st === "running" && prog != null ? Math.round(prog * 100) : null;
          const eta = st === "running" ? fmtEta(job.stages[s]?.startedAt, prog) : null;
          return (
            <li key={s} className="flex flex-col">
              <div className="flex items-center gap-2.5 text-[13px]">
                {st === "done" && <Check className="h-3.5 w-3.5 text-emerald-600" />}
                {st === "running" && <Loader2 className="h-3.5 w-3.5 animate-spin text-accent" />}
                {st === "failed" && <AlertCircle className="h-3.5 w-3.5 text-danger" />}
                {(st === "pending" || st === "skipped") && <Circle className="h-3.5 w-3.5 text-muted/40" />}
                <span className={cn(st === "pending" && "text-muted", st === "skipped" && "text-muted line-through")}>
                  {st === "done" ? l.done : l.doing}
                </span>
                {pct != null && <span className="font-mono text-[11.5px] tabular-nums text-accent">{pct}%</span>}
                {eta && <span className="text-[11.5px] text-muted">{eta}</span>}
                {st === "done" && job.stages[s]?.detail && (
                  // meetings processed before the detail was trimmed still carry "· 19 turns …"
                  <span className="truncate text-[11px] text-muted">{s === "diarizing" ? job.stages[s]!.detail!.split(" · ")[0] : job.stages[s]!.detail}</span>
                )}
                {st === "failed" && !running && !queued && (
                  <Button size="sm" variant="secondary" className="ml-auto" onClick={() => onRetry(s)}>
                    <RotateCw className="h-3 w-3" /> {l.retry}
                  </Button>
                )}
              </div>
              {st === "running" && pct != null && (
                <div className="ml-6 mt-1 h-1 w-56 overflow-hidden rounded bg-fg/10"><div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} /></div>
              )}
              {st === "failed" && (
                <div className="ml-6 mt-1 text-[12px] text-danger/90">
                  {job.stages[s]?.error}
                  {job.stages[s]?.errorDetail && (
                    <button className="ml-2 inline-flex items-center gap-0.5 text-[11px] text-muted hover:text-fg" onClick={() => setShowDetail(showDetail === s ? null : s)}>
                      Details <ChevronDown className={cn("h-3 w-3 transition-transform", showDetail === s && "rotate-180")} />
                    </button>
                  )}
                  {showDetail === s && (
                    <pre className="selectable mt-1 max-h-48 overflow-auto rounded bg-fg/[0.04] p-2 font-mono text-[10.5px] leading-snug text-fg/70">{job.stages[s]?.errorDetail}</pre>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
