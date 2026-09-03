import { Fragment, useEffect, useMemo, useRef } from "react";
import type { MeetingSpeaker, TranscriptSegment } from "@/types/engine";
import { fmtTime } from "@/lib/format";
import { cn, speakerColor } from "@/lib/utils";

export function speakerDisplay(s: MeetingSpeaker | undefined): string {
  if (!s) return "Speaker";
  return s.displayName || s.speakerName || s.label;
}

export function TranscriptView({
  segments,
  speakers,
  currentTime,
  activeSegmentId,
  onSeek,
  onSpeakerClick,
}: {
  segments: TranscriptSegment[];
  speakers: MeetingSpeaker[];
  currentTime: number;
  activeSegmentId?: number | null;
  onSeek: (t: number) => void;
  onSpeakerClick: (s: MeetingSpeaker) => void;
}) {
  const byId = useMemo(() => new Map(speakers.map((s) => [s.id, s])), [speakers]);
  const activeRef = useRef<HTMLDivElement>(null);

  // Group consecutive segments by speaker into turns.
  const turns = useMemo(() => {
    const out: { speakerId: number | null; segs: TranscriptSegment[] }[] = [];
    for (const s of segments) {
      const last = out[out.length - 1];
      if (last && last.speakerId === s.meetingSpeakerId && s.start - last.segs[last.segs.length - 1].end < 6) last.segs.push(s);
      else out.push({ speakerId: s.meetingSpeakerId, segs: [s] });
    }
    return out;
  }, [segments]);

  const playingId = useMemo(() => {
    const s = segments.find((x) => currentTime >= x.start && currentTime < x.end);
    return s?.id ?? null;
  }, [segments, currentTime]);

  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [activeSegmentId]);

  if (!segments.length) return <div className="text-[13px] text-muted">No transcript yet.</div>;

  return (
    <div className="selectable flex flex-col gap-4">
      {turns.map((t, i) => {
        const sp = t.speakerId != null ? byId.get(t.speakerId) : undefined;
        const color = speakerColor(sp?.colorIndex ?? 0);
        return (
          <div key={i} className="grid grid-cols-[120px_1fr] gap-3">
            <div className="pt-[2px]">
              <button
                className={cn("group flex max-w-full items-center gap-1.5 rounded px-1 py-0.5 text-left text-[12px] font-semibold", color.text, "hover:bg-fg/[0.05]")}
                onClick={() => sp && onSpeakerClick(sp)}
                title="Rename speaker"
              >
                <span className={cn("h-2.5 w-2.5 shrink-0 rounded-full", color.dot)} />
                <span className="truncate">{speakerDisplay(sp)}</span>
              </button>
              <button
                className="ml-[18px] block font-mono text-[11px] tabular-nums text-muted hover:text-accent"
                onClick={() => onSeek(t.segs[0].start)}
              >
                {fmtTime(t.segs[0].start)}
              </button>
            </div>
            <p className="text-[14px] leading-[1.65] text-fg/90">
              {t.segs.map((s) => (
                <Fragment key={s.id}>
                  <span
                    ref={s.id === activeSegmentId ? activeRef : undefined}
                    onClick={() => onSeek(s.start)}
                    className={cn(
                      "rounded-sm transition-colors cursor-default",
                      s.id === playingId && "bg-accent/15",
                      s.id === activeSegmentId && "bg-amber-400/25",
                      s.id !== playingId && "hover:bg-fg/[0.05]",
                    )}
                    title={fmtTime(s.start)}
                  >
                    {s.text}
                  </span>{" "}
                </Fragment>
              ))}
            </p>
          </div>
        );
      })}
    </div>
  );
}
