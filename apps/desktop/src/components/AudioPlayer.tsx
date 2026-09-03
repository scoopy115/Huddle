import { useEffect, useRef, useState } from "react";
import { native } from "@/lib/native";
import { Pause, Play, RotateCcw, RotateCw } from "lucide-react";
import { fmtTime } from "@/lib/format";
import { Button } from "./ui";

export interface PlayerHandle {
  seekTo: (sec: number, play?: boolean) => void;
}

export function AudioPlayer({
  meetingId,
  path,
  durationHint,
  onTime,
  handleRef,
}: {
  meetingId: string;
  path: string | null;
  durationHint?: number | null;
  onTime?: (t: number) => void;
  handleRef: React.MutableRefObject<PlayerHandle | null>;
}) {
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(durationHint ?? 0);
  const [rate, setRate] = useState(1);
  const src = path ? native.audioSrc(meetingId, path) : null;

  useEffect(() => {
    handleRef.current = {
      seekTo: (sec, play = true) => {
        const a = audio.current;
        if (!a) return;
        a.currentTime = Math.max(0, sec);
        if (play) a.play().catch(() => {});
      },
    };
  }, [handleRef]);

  useEffect(() => {
    const a = audio.current;
    if (!a) return;
    const onT = () => {
      setTime(a.currentTime);
      onTime?.(a.currentTime);
    };
    const onD = () => setDuration(isFinite(a.duration) ? a.duration : durationHint ?? 0);
    a.addEventListener("timeupdate", onT);
    a.addEventListener("durationchange", onD);
    a.addEventListener("play", () => setPlaying(true));
    a.addEventListener("pause", () => setPlaying(false));
    a.addEventListener("ended", () => setPlaying(false));
    return () => {
      a.removeEventListener("timeupdate", onT);
      a.removeEventListener("durationchange", onD);
    };
  }, [onTime, durationHint, src]);

  useEffect(() => {
    if (audio.current) audio.current.playbackRate = rate;
  }, [rate]);

  if (!src) {
    return <div className="rounded-lg border border-dashed border-border px-4 py-3 text-[12px] text-muted">Audio not available for this meeting.</div>;
  }

  const toggle = () => {
    const a = audio.current;
    if (!a) return;
    if (a.paused) a.play().catch(() => {});
    else a.pause();
  };
  const skip = (d: number) => {
    const a = audio.current;
    if (a) a.currentTime = Math.min(Math.max(0, a.currentTime + d), duration || a.currentTime + d);
  };

  return (
    <div className="flex items-center gap-3 panel px-3 py-2">
      <audio ref={audio} src={src} preload="metadata" />
      <Button variant="ghost" size="sm" onClick={() => skip(-10)} title="Back 10 s"><RotateCcw className="h-3.5 w-3.5" /></Button>
      <Button variant="record" size="sm" className="h-8 w-8 rounded-full p-0" onClick={toggle}>
        {playing ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5 translate-x-px" />}
      </Button>
      <Button variant="ghost" size="sm" onClick={() => skip(10)} title="Forward 10 s"><RotateCw className="h-3.5 w-3.5" /></Button>
      <span className="w-[44px] text-right font-mono text-[11px] tabular-nums text-muted">{fmtTime(time)}</span>
      <input
        type="range"
        min={0}
        max={duration || 1}
        step={0.1}
        value={Math.min(time, duration || 1)}
        onChange={(e) => {
          const a = audio.current;
          if (a) a.currentTime = Number(e.target.value);
        }}
        className="flex-1 accent-[rgb(var(--accent))]"
      />
      <span className="w-[44px] font-mono text-[11px] tabular-nums text-muted">{fmtTime(duration)}</span>
      <button
        className="rounded px-1.5 py-0.5 text-[11px] font-medium text-muted hover:bg-fg/[0.06]"
        onClick={() => setRate(rate === 1 ? 1.25 : rate === 1.25 ? 1.5 : rate === 1.5 ? 2 : 1)}
      >
        {rate}×
      </button>
    </div>
  );
}
