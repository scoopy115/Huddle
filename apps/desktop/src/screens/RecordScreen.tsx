import { useCallback, useEffect, useRef, useState } from "react";
import { Languages, Laptop, Mic, Monitor, NotebookPen, Pause, Play, RefreshCw, Server, Square, Users, X } from "lucide-react";
import { languageOptions } from "@/lib/languages";
import { MODE_OPTIONS, SPEAKER_COUNT_OPTIONS, speakerCountLabel } from "@/components/MeetingMenu";
import { resetAudio, sounds } from "@/lib/sounds";
import { native, type InputDevice, type RecordingMeta, type SystemAudioSupport } from "@/lib/native";
import { api, errorMessage } from "@/lib/api";
import { fmtTime } from "@/lib/format";
import { useNav } from "@/lib/nav";
import type { LiveStatus, MeetingMode, ProcessingTarget, UserSettings } from "@/types/engine";
import { Button, Dialog, Select } from "@/components/ui";

/** Where a new recording goes: the setting decides, "ask" opens the chooser (only when a server is set up). */
export function processingChoice(settings: UserSettings): ProcessingTarget | "ask" {
  if (!settings["server.url"] || !settings["server.apiKey"]) return "local";
  return settings["server.defaultTarget"] ?? "ask";
}

const BARS = 56;

export function RecordScreen({
  settings,
  onSettings,
  onRecordingStateChange,
}: {
  settings: UserSettings;
  onSettings: (p: Partial<UserSettings>) => void;
  onRecordingStateChange: (recording: boolean) => void;
}) {
  const { go } = useNav();
  const [devices, setDevices] = useState<InputDevice[]>([]);
  const [device, setDevice] = useState<string | null>(settings["recording.inputDevice"]);
  const [systemAudio, setSystemAudio] = useState<boolean>(settings["recording.systemAudio"]);
  const [language, setLanguage] = useState<string>("auto");
  const [speakerCount, setSpeakerCount] = useState(0);
  const [mode, setMode] = useState<MeetingMode>("meeting");
  const [stopped, setStopped] = useState<RecordingMeta | null>(null);   // waiting for the "where to process" choice
  const [support, setSupport] = useState<SystemAudioSupport | null>(null);
  const [meta, setMeta] = useState<RecordingMeta | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [paused, setPaused] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [live, setLive] = useState<LiveStatus | null>(null);
  const levels = useRef<number[]>(new Array(BARS).fill(0));
  const sysLevels = useRef<number[]>(new Array(BARS).fill(0));
  const canvas = useRef<HTMLCanvasElement>(null);
  const raf = useRef(0);

  const mics = devices.filter((d) => !d.isLoopback);
  const refreshSupport = useCallback(() => native.systemAudioSupport().then(setSupport).catch(() => {}), []);

  useEffect(() => {
    native.listInputDevices().then((d) => {
      setDevices(d);
      if (!device && d.length) setDevice(d.find((x) => x.isDefault && !x.isLoopback)?.name ?? d[0].name);
    }).catch(() => {});
    native.recordingStatus().then((s) => { if (s.recording && s.meta) { setMeta(s.meta); setElapsed(s.elapsedSec); setPaused(s.paused); } });
    refreshSupport();
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    // Loudness on a dB scale: −50 dBFS (room noise) is silent, −30 dBFS (normal speaking
    // voice at a metre) already fills half the bar, −10 dBFS is full. Linear RMS made
    // ordinary speech look like a whisper.
    const level = (rms: number) => Math.min(1, Math.max(0, (20 * Math.log10(Math.max(rms, 1e-6)) + 50) / 40));
    native.onLevel((e) => {
      levels.current.push(level(e.rms));
      if (levels.current.length > BARS) levels.current.shift();
      sysLevels.current.push(level(e.systemRms ?? 0));
      if (sysLevels.current.length > BARS) sysLevels.current.shift();
      setElapsed(e.elapsedSec);
    }).then((u) => (unlisten = u));
    return () => unlisten?.();
  }, []);
  // Start, stop and pause can also come from the menu bar or ⌥⌘R; follow the shell's state.
  // A recording stopped there is turned into a meeting by App (pending queue), not here.
  useEffect(() => {
    const uns: (() => void)[] = [];
    native.onRecordingStarted((m) => {
      setMeta(m);
      setElapsed(0);
      setPaused(false);
      setError(null);
      levels.current.fill(0);
      sysLevels.current.fill(0);
    }).then((u) => uns.push(u));
    native.onRecordingStopped(() => { setMeta(null); setPaused(false); }).then((u) => uns.push(u));
    native.onRecordingPaused(() => setPaused(true)).then((u) => uns.push(u));
    native.onRecordingResumed(() => setPaused(false)).then((u) => uns.push(u));
    return () => uns.forEach((u) => u());
  }, []);

  useEffect(() => {
    if (!meta) { setLive(null); return; }
    const t = setInterval(() => api.liveStatus(meta.id).then(setLive).catch(() => {}), 3000);
    return () => clearInterval(t);
  }, [meta]);

  const draw = useCallback(() => {
    const c = canvas.current;
    if (c) {
      const ctx = c.getContext("2d")!;
      const dpr = window.devicePixelRatio || 1;
      const w = c.clientWidth, h = c.clientHeight;
      if (c.width !== w * dpr) { c.width = w * dpr; c.height = h * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const gap = 3, bw = (w - gap * (BARS - 1)) / BARS;
      const rec = getComputedStyle(document.documentElement).getPropertyValue("--record").trim();
      const ink = getComputedStyle(document.documentElement).getPropertyValue("--fg").trim();
      for (let i = 0; i < BARS; i++) {
        const v = levels.current[i] ?? 0;
        const sv = meta?.systemFilePath ? sysLevels.current[i] ?? 0 : 0;
        const x = i * (bw + gap);
        const bh = Math.max(3, v * h);
        // Idle: a calm resting line. Recording: bars fade in with level.
        ctx.fillStyle = `rgb(${rec} / ${meta ? 0.3 + v * 0.7 : 0.18})`;
        ctx.beginPath(); ctx.roundRect(x, (h - bh) / 2, bw, bh, 2); ctx.fill();
        if (sv > 0) {
          const sh = Math.max(2, sv * h * 0.6);
          ctx.fillStyle = `rgb(${ink} / 0.35)`;
          ctx.beginPath(); ctx.roundRect(x, (h - sh) / 2, bw, sh, 2); ctx.fill();
        }
      }
    }
    raf.current = requestAnimationFrame(draw);
  }, [meta]);

  useEffect(() => { raf.current = requestAnimationFrame(draw); return () => cancelAnimationFrame(raf.current); }, [draw]);

  const permissionOk = !systemAudio || support?.supported !== false;

  const start = async () => {
    setError(null);
    setBusy(true);
    try {
      const m = await native.startRecording(device, systemAudio, null);
      resetAudio();
      sounds.recordStart();
      setMeta(m);
      setElapsed(0);
      onRecordingStateChange(true);
      api.liveStart(m.id, m.filePath, language === "auto" ? null : language).then(setLive).catch(() => { /* live text is best-effort */ });
    } catch (e) {
      const msg = errorMessage(e);
      setError(msg);
    } finally { setBusy(false); }
  };

  const togglePause = async () => {
    try {
      const s = paused ? await native.resumeRecording() : await native.pauseRecording();
      setPaused(s.paused);
      sounds.tap();
    } catch (e) { setError(errorMessage(e)); }
  };

  const stop = async () => {
    setBusy(true);
    try {
      const m = await native.stopRecording();
      resetAudio();
      sounds.recordStop();
      onRecordingStateChange(false);
      setMeta(null);
      setPaused(false);
      if (m.status !== "saved") { setError(m.error ?? "The recording could not be saved."); return; }
      if (m.durationSec < 1) { setError("The recording was too short to keep."); return; }
      try { await api.liveStop(m.id, true); } catch { /* fall back to a full transcription */ }
      const choice = processingChoice(settings);
      if (choice === "ask") { setStopped(m); return; }
      await submit(m, choice);
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  };

  const submit = async (m: RecordingMeta, target: ProcessingTarget) => {
    setStopped(null);
    setBusy(true);
    try {
      const meeting = await api.createFromRecording({
        id: m.id, filePath: m.filePath, systemFilePath: m.systemFilePath ?? null, startedAt: m.startedAt, durationSec: m.durationSec,
        inputDevice: m.inputDevice, sampleRate: m.sampleRate, channels: m.channels, format: m.format, source: "recorded", process: true,
        language: language === "auto" ? null : language,
        speakerCount: speakerCount || null,
        mode,
        processingTarget: target,
      });
      if (m.error) setError(m.error);
      go({ kind: "meeting", id: meeting.id });
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  };

  return (
    <div className="relative flex h-full flex-col">
      {/* Warm red wash behind the timer; intensifies while recording. */}
      <div className={`pointer-events-none absolute inset-0 transition-opacity duration-700 wash-accent ${meta ? "opacity-100" : "opacity-60"}`} />
      <header data-tauri-drag-region className="titlebar-drag relative flex h-[52px] shrink-0 items-center px-5">
        <div data-tauri-drag-region className="flex-1" />
        {!meta && <Button variant="ghost" size="sm" onClick={() => go({ kind: "meetings" })}><X className="h-4 w-4" /></Button>}
      </header>
      <div className="relative flex flex-1 flex-col items-center justify-center gap-7 px-8 pb-16">
        <div className="relative text-center">
          {meta && !paused && (
            <>
              <span className="pointer-events-none absolute left-1/2 top-1/2 h-[220px] w-[220px] -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-accent/40 animate-ring" />
              <span className="pointer-events-none absolute left-1/2 top-1/2 h-[220px] w-[220px] -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-accent/40 animate-ring [animation-delay:1.2s]" />
            </>
          )}
          <div className="relative font-display text-[72px] font-bold leading-none tabular-nums tracking-tight">{fmtTime(elapsed)}</div>
          <div className="relative mt-3 flex items-center justify-center gap-2 text-[13px] text-muted">
            {meta ? (paused ? <><Pause className="h-3.5 w-3.5" /> Paused</> : <><span className="h-2 w-2 rounded-full bg-record animate-record" /> Recording{meta.systemFilePath ? " with system audio" : ""}</>) : <><Mic className="h-3.5 w-3.5" /> Ready to record</>}
          </div>
        </div>

        <canvas ref={canvas} className="h-[72px] w-full max-w-[560px]" />

        {!meta ? (
          <div className="panel flex w-full max-w-[440px] flex-col items-stretch gap-3 p-4">
            <label className="flex items-center justify-between gap-3 text-[13px]">
              <span className="inline-flex items-center gap-2 text-muted"><Mic className="h-3.5 w-3.5" /> Microphone
                <button className="text-muted hover:text-accent" title="Refresh devices — an iPhone nearby shows up here as a microphone" onClick={(e) => { e.preventDefault(); native.listInputDevices().then(setDevices).catch(() => {}); }}><RefreshCw className="h-3 w-3" /></button>
              </span>
              <Select value={device ?? ""} onChange={(e) => { setDevice(e.target.value || null); onSettings({ "recording.inputDevice": e.target.value || null }); }}>
                {mics.length === 0 && <option value="">System default microphone</option>}
                {mics.map((d) => <option key={d.id} value={d.name}>{d.name}{d.isDefault ? " (default)" : ""}</option>)}
              </Select>
            </label>
            <label className="flex items-center justify-between gap-3 text-[13px]">
              <span className="inline-flex items-center gap-2 text-muted"><Monitor className="h-3.5 w-3.5" /> System audio</span>
              <Select value={systemAudio ? "all" : "off"} onChange={(e) => { const v = e.target.value === "all"; setSystemAudio(v); onSettings({ "recording.systemAudio": v }); if (v) refreshSupport(); }}>
                <option value="off">Off</option>
                <option value="all">All apps (online meetings, calls)</option>
              </Select>
            </label>
            <label className="flex items-center justify-between gap-3 text-[13px]">
              <span className="inline-flex items-center gap-2 text-muted"><Languages className="h-3.5 w-3.5" /> Spoken language</span>
              <Select value={language} onChange={(e) => setLanguage(e.target.value)}>
                <option value="auto">Detect automatically</option>
                {languageOptions().map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
              </Select>
            </label>
            <label className="flex items-center justify-between gap-3 text-[13px]">
              <span className="inline-flex items-center gap-2 text-muted"><Users className="h-3.5 w-3.5" /> People speaking</span>
              <Select value={String(speakerCount)} onChange={(e) => setSpeakerCount(Number(e.target.value))}>
                {SPEAKER_COUNT_OPTIONS.map((n) => <option key={n} value={n}>{speakerCountLabel(n)}</option>)}
              </Select>
            </label>
            <label className="flex items-center justify-between gap-3 text-[13px]">
              <span className="inline-flex items-center gap-2 text-muted" title={MODE_OPTIONS.find((o) => o.value === mode)?.hint}><NotebookPen className="h-3.5 w-3.5" /> Notes style</span>
              <Select value={mode} onChange={(e) => setMode(e.target.value as MeetingMode)}>
                {MODE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </Select>
            </label>
            {systemAudio && support && !support.supported && (
              <div className="rounded-lg border border-border bg-bg px-3 py-2.5 text-[12.5px]">{support.message}</div>
            )}
            <Button variant="record" size="lg" loading={busy} onClick={start} className="mt-1 rounded-full" disabled={!permissionOk}>
              <span className="h-2.5 w-2.5 rounded-full bg-white" /> Start Recording
            </Button>
          </div>
        ) : (
          <div className="flex w-full max-w-[560px] flex-col items-center gap-3">
            <div className="flex items-center gap-2">
              <Button variant="secondary" size="lg" disabled={busy} onClick={togglePause} className="rounded-full px-6" title={paused ? "Continue recording" : "Pause — nothing is recorded until you resume"}>
                {paused ? <><Play className="h-3.5 w-3.5 fill-current" /> Resume</> : <><Pause className="h-3.5 w-3.5 fill-current" /> Pause</>}
              </Button>
              <Button variant="primary" size="lg" loading={busy} onClick={stop} className="rounded-full px-7"><Square className="h-3.5 w-3.5 fill-current" /> Stop Recording</Button>
            </div>
            <div className="text-[12px] text-muted">{meta.inputDevice}{meta.systemFilePath ? " + system audio" : ""}</div>
            {live && live.state !== "failed" && (
              <div className="panel mt-2 w-full px-4 py-3 text-[12.5px]">
                <div className="mb-1 flex items-center justify-between text-muted">
                  <span className="inline-flex items-center gap-2"><span className="h-[10px] w-[3px] rounded-full bg-accent" /> Transcribing while you record</span>
                  <span className="font-mono tabular-nums">{fmtTime(live.processedSec)} done</span>
                </div>
                {live.recent.length > 0 && <p className="selectable line-clamp-2 text-fg/70">{live.recent.map((r) => r.text).join(" ")}</p>}
              </div>
            )}
          </div>
        )}

        {error && <div className="max-w-[460px] rounded-lg border border-danger/30 bg-danger/5 px-3 py-2 text-center text-[12.5px] text-danger">{error}</div>}
      </div>

      <Dialog open={!!stopped} onClose={() => stopped && submit(stopped, "local")} title="Process where?" width={440}>
        <div className="grid grid-cols-2 gap-2">
          <button className="pressable flex flex-col items-start gap-1.5 rounded-xl border border-border p-3 text-left hover:border-fg/30" onClick={() => stopped && submit(stopped, "local")}>
            <Laptop className="h-4 w-4 text-muted" />
            <span className="text-[13px] font-medium">This Mac</span>
            <span className="text-[11.5px] text-muted">Everything stays here.</span>
          </button>
          <button className="pressable flex flex-col items-start gap-1.5 rounded-xl border border-accent/40 bg-accent-soft/40 p-3 text-left hover:border-accent" onClick={() => stopped && submit(stopped, "remote")}>
            <Server className="h-4 w-4 text-accent" />
            <span className="text-[13px] font-medium">{settings["server.name"] || "The server"}</span>
            <span className="text-[11.5px] text-muted">Sent over TLS, result comes back here.</span>
          </button>
        </div>
        <p className="mt-3 text-[11.5px] text-muted">Change the default under Settings → Server.</p>
      </Dialog>
    </div>
  );
}
