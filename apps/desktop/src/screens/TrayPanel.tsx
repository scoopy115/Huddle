import { useCallback, useEffect, useRef, useState } from "react";
import { ExternalLink, Mic, Monitor, Power, Square } from "lucide-react";
import { native, type InputDevice, type RecordingMeta, type ShellPrefs } from "@/lib/native";
import { fmtTime } from "@/lib/format";
import { modKey } from "@/lib/utils";
import { BrandMark } from "@/components/ui";

const BARS = 40;

/**
 * The menu-bar popover: the recording screen boiled down to a timer, a level meter and one
 * button. It runs in its own small window, talks to the shell only (the engine may be asleep),
 * and never opens the main window itself — stopping does that, from the Rust side.
 */
export function TrayPanel() {
  const [meta, setMeta] = useState<RecordingMeta | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [prefs, setPrefs] = useState<ShellPrefs | null>(null);
  const [devices, setDevices] = useState<InputDevice[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const levels = useRef<number[]>(new Array(BARS).fill(0));
  const canvas = useRef<HTMLCanvasElement>(null);
  const raf = useRef(0);

  const refresh = useCallback(() => {
    native
      .recordingStatus()
      .then((s) => {
        setMeta(s.recording ? s.meta : null);
        setElapsed(s.recording ? s.elapsedSec : 0);
        setBusy(false);
      })
      .catch(() => {});
    native
      .getShellPrefs()
      .then(setPrefs)
      .catch(() => {});
    native
      .listInputDevices()
      .then(setDevices)
      .catch(() => {});
  }, []);
  const mics = devices.filter((d) => !d.isLoopback);
  const save = (patch: Partial<ShellPrefs>) =>
    native.setShellPrefs(patch).then(setPrefs).catch((e) => setError(String(e)));

  useEffect(() => {
    refresh();
    const uns: (() => void)[] = [];
    native
      .onRecordingStarted((m) => {
        setMeta(m);
        setElapsed(0);
        setBusy(false);
        setError(null);
      })
      .then((u) => uns.push(u));
    native.onRecordingWarning(setWarning).then((u) => uns.push(u));
    native
      .onRecordingStopped(() => {
        setMeta(null);
        setBusy(false);
        setWarning(null);
        levels.current.fill(0);
      })
      .then((u) => uns.push(u));
    native
      .onRecordingError((e) => {
        setError(e);
        setBusy(false);
      })
      .then((u) => uns.push(u));
    native
      .onTrayShown(() => {
        setError(null);
        refresh();
      })
      .then((u) => uns.push(u));
    const level = (rms: number) =>
      Math.min(
        1,
        Math.max(0, (20 * Math.log10(Math.max(rms, 1e-6)) + 50) / 40),
      );
    native
      .onLevel((e) => {
        levels.current.push(level(e.rms));
        if (levels.current.length > BARS) levels.current.shift();
        setElapsed(e.elapsedSec);
      })
      .then((u) => uns.push(u));
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") native.trayHide();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      uns.forEach((u) => u());
      window.removeEventListener("keydown", onKey);
    };
  }, [refresh]);

  useEffect(() => {
    const draw = () => {
      const c = canvas.current;
      if (c) {
        const ctx = c.getContext("2d")!;
        const dpr = window.devicePixelRatio || 1;
        const w = c.clientWidth,
          h = c.clientHeight;
        if (c.width !== w * dpr) {
          c.width = w * dpr;
          c.height = h * dpr;
        }
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, w, h);
        const gap = 2.5,
          bw = (w - gap * (BARS - 1)) / BARS;
        const rec = getComputedStyle(document.documentElement)
          .getPropertyValue("--record")
          .trim();
        for (let i = 0; i < BARS; i++) {
          const v = levels.current[i] ?? 0;
          const bh = Math.max(2.5, v * h);
          ctx.fillStyle = `rgb(${rec} / ${meta ? 0.3 + v * 0.7 : 0.18})`;
          ctx.beginPath();
          ctx.roundRect(i * (bw + gap), (h - bh) / 2, bw, bh, 1.5);
          ctx.fill();
        }
      }
      raf.current = requestAnimationFrame(draw);
    };
    raf.current = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf.current);
  }, [meta]);

  const toggle = () => {
    setError(null);
    setBusy(true);
    native.trayToggleRecording().catch((e) => {
      setError(String(e));
      setBusy(false);
    });
  };

  return (
    <div className="flex h-screen w-screen flex-col items-stretch px-2 pb-2 pt-[9px] text-fg">
      {/* Arrow pointing at the menu-bar icon. */}
      <div className="relative z-10 mx-auto -mb-[7px] h-[14px] w-[14px] rotate-45 rounded-[3px] border-l border-t border-border bg-surface" />
      <div className="panel relative flex flex-1 flex-col overflow-hidden px-4 pb-3 pt-3.5 shadow-2xl">
        <div
          className={`pointer-events-none absolute inset-0 transition-opacity duration-700 wash-accent ${meta ? "opacity-100" : "opacity-50"}`}
        />

        <header className="relative flex items-center gap-2">
          <BrandMark className="h-[18px] w-[18px]" />
          <span className="font-display text-[13.5px] font-bold tracking-tight">
            Huddle
          </span>
          <span className="ml-auto rounded-md border border-border px-1.5 py-0.5 font-mono text-[10.5px] text-muted">
            {modKey}⌥R
          </span>
        </header>

        <div className="relative mt-4 text-center">
          <div className="font-display text-[42px] font-bold leading-none tabular-nums tracking-tight">
            {fmtTime(elapsed)}
          </div>
          <div className="mt-2 flex items-center justify-center gap-1.5 text-[12px] text-muted">
            {meta ? (
              <>
                <span className="h-2 w-2 rounded-full bg-record animate-record" />{" "}
                Recording{meta.systemFilePath ? " with system audio" : ""}
              </>
            ) : (
              <>
                <Mic className="h-3.5 w-3.5" /> Ready to record
              </>
            )}
          </div>
        </div>

        <canvas ref={canvas} className="relative mt-3 h-[44px] w-full" />

        <div className="relative mt-3 flex flex-col items-center gap-1.5">
          <button
            onClick={toggle}
            disabled={busy}
            aria-label={meta ? "Stop recording" : "Start recording"}
            className={`pressable flex h-[60px] w-[60px] items-center justify-center rounded-full shadow-md transition-colors disabled:opacity-60 ${meta ? "bg-ink text-ink-fg" : "bg-record text-white glow-accent hover:brightness-110"}`}
          >
            {meta ? (
              <Square className="h-5 w-5 fill-current" />
            ) : (
              <span className="h-5 w-5 rounded-full bg-white" />
            )}
          </button>
          <div className="text-[12px] font-medium">
            {meta ? "Stop and open in Huddle" : "Start Recording"}
          </div>
        </div>

        {!meta && prefs && (
          <div className="relative mt-3 flex flex-col gap-1.5 text-[11.5px]">
            <label className="flex items-center justify-between gap-2">
              <span className="flex shrink-0 items-center gap-1.5 text-muted">
                <Mic className="h-3 w-3" /> Microphone
              </span>
              <select
                className="h-7 min-w-0 flex-1 rounded-md border border-border bg-surface px-1.5 text-[11.5px] text-fg"
                value={prefs.inputDevice ?? ""}
                onChange={(e) => save({ inputDevice: e.target.value || null })}
              >
                <option value="">System default</option>
                {mics.map((d) => (
                  <option key={d.id} value={d.name}>
                    {d.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center justify-between gap-2">
              <span className="flex shrink-0 items-center gap-1.5 text-muted">
                <Monitor className="h-3 w-3" /> System audio
              </span>
              <select
                className="h-7 min-w-0 flex-1 rounded-md border border-border bg-surface px-1.5 text-[11.5px] text-fg"
                value={prefs.systemAudio ? "all" : "off"}
                onChange={(e) => save({ systemAudio: e.target.value === "all" })}
              >
                <option value="off">Off</option>
                <option value="all">All apps</option>
              </select>
            </label>
          </div>
        )}

        {error && (
          <div className="relative mt-2 rounded-md border border-danger/30 bg-danger/5 px-2.5 py-1.5 text-center text-[11.5px] text-danger">
            {error}
          </div>
        )}
        {warning && (
          <div className="relative mt-2 rounded-md border border-border bg-bg px-2.5 py-1.5 text-center text-[11px] text-muted">
            {warning}{" "}
            <button className="underline hover:text-fg" onClick={() => native.openSystemAudioSettings()}>Open System Settings</button>
          </div>
        )}

        <div className="relative mt-auto flex items-center justify-between border-t border-border pt-2 text-[12px]">
          <button
            className="pressable flex items-center gap-1.5 rounded-md px-2 py-1 text-fg/75 hover:bg-fg/[0.05] hover:text-fg"
            onClick={() => native.trayOpenMain()}
          >
            <ExternalLink className="h-3.5 w-3.5" /> Open Huddle
          </button>
          <button
            className="pressable flex items-center gap-1.5 rounded-md px-2 py-1 text-fg/75 hover:bg-fg/[0.05] hover:text-fg"
            onClick={() => native.trayQuit()}
          >
            <Power className="h-3.5 w-3.5" /> Quit
          </button>
        </div>
      </div>
    </div>
  );
}
