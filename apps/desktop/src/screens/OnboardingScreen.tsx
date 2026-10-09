import { useEffect, useState } from "react";
import { Check, Circle, Download, Loader2, XCircle } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { DownloadCandidate, DownloadProgress, Resolution, SetupPlan } from "@/types/engine";
import { fmtBytes } from "@/lib/format";
import { native } from "@/lib/native";
import { PermissionsPanel, allGranted, usePermissions } from "@/components/PermissionsPanel";
import { HardwareCard } from "@/components/HardwareCard";
import { cn, isMac, platformName } from "@/lib/utils";
import { Button, Switch } from "@/components/ui";
import logo from "@/assets/huddle-logo.svg";

const TASK_LABEL: Record<string, string> = { transcription: "Transcription", diarization: "Speaker detection", llm: "AI notes" };

/** One step per model, in the order they matter: transcription (required, with a choice of
 *  model), speaker detection (ships with the app; on or off), AI notes (optional: download or
 *  skip). macOS adds a permissions step. `returning`: the transcription model went missing after
 *  onboarding; only that step is shown. */
type Step = "scan" | "transcription" | "speakers" | "ai" | "permissions";

export function OnboardingScreen({ onDone, returning = false }: { onDone: () => void; returning?: boolean }) {
  const [scan, setScan] = useState(0);
  const [step, setStep] = useState<Step>("scan");
  const [plan, setPlan] = useState<SetupPlan | null>(null);
  const [candidates, setCandidates] = useState<DownloadCandidate[]>([]);
  const [choice, setChoice] = useState<string | null>(null);          // transcription candidate id
  const [error, setError] = useState<string | null>(null);
  const [downloads, setDownloads] = useState<DownloadProgress[]>([]);
  const [downloading, setDownloading] = useState<string | null>(null);   // candidate id in flight
  const [diarization, setDiarization] = useState(true);

  useEffect(() => {
    api.getSettings().then((s) => setDiarization(s["speakers.diarization"] !== false)).catch(() => {});
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        for (let i = 1; i <= 3; i++) { await new Promise((r) => setTimeout(r, 450)); if (alive) setScan(i); }
        const [p, c] = await Promise.all([api.setupPlan(), api.candidates().catch(() => [] as DownloadCandidate[])]);
        if (!alive) return;
        setPlan(p);
        setCandidates(c);
        setChoice(p.resolutions.find((r) => r.task === "transcription")?.download?.id ?? null);
        setStep("transcription");
      } catch (e) { if (alive) setError(errorMessage(e)); }
    })();
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!downloading) return;
    const t = setInterval(async () => {
      const list = await api.downloads();
      setDownloads(list);
      const mine = list.find((d) => d.id === downloading);
      if (mine && (mine.state === "done" || mine.state === "failed" || mine.state === "cancelled")) {
        setDownloading(null);
        // A model picked by hand (not the recommended one) stays the choice in Settings → Models.
        const recommended = plan?.resolutions.find((r) => r.task === "transcription")?.download?.id;
        if (mine.state === "done" && mine.modelId && mine.id !== recommended && candidates.some((c) => c.id === mine.id && c.task === "transcription")) {
          await api.updateSettings({ "models.whisper": mine.modelId }).catch(() => {});
        }
        setPlan(await api.setupPlan());
      }
    }, 700);
    return () => clearInterval(t);
  }, [downloading, plan, candidates]);

  const res = (task: string) => plan?.resolutions.find((r) => r.task === task);
  const ok = (r?: Resolution) => r?.status === "ready" || r?.status === "builtin";
  const download = async (id: string) => { setDownloading(id); await api.startDownload(id); };
  const finish = async () => { await api.updateSettings({ "onboarding.completed": true }); onDone(); };
  // The permissions step asks macOS for the two recording permissions while the user is watching,
  // so the prompts never appear later from a menu-bar recording (a background prompt bounces the
  // Dock). Windows has no such prompts.
  const afterAi = () => { if (isMac) { setStep("permissions"); } else { finish(); } };

  const steps: { id: Step; label: string }[] = returning
    ? [{ id: "transcription", label: "Transcription" }]
    : [{ id: "transcription", label: "Transcription" }, { id: "speakers", label: "Speakers" }, { id: "ai", label: "AI notes" }, ...(isMac ? [{ id: "permissions" as Step, label: "Recording" }] : [])];
  const current = steps.findIndex((s) => s.id === step);

  const transcription = res("transcription");
  const llm = res("llm");
  const progressOf = (id?: string | null) => downloads.find((d) => d.id === id);
  const options = candidates.filter((c) => c.task === "transcription" && c.fit !== "no");
  const chosen = options.find((c) => c.id === choice) ?? options[0];

  return (
    <div className="flex h-full flex-col">
      <div data-tauri-drag-region className="titlebar-drag titlebar-space h-[38px] shrink-0" />
      {/* Scrolls when the window is shorter than a step (centred otherwise). */}
      <div className="flex min-h-0 flex-1 justify-center overflow-y-auto px-8 pb-12">
        <div className="my-auto w-full max-w-[460px] py-4">
          <img src={logo} alt="Huddle" className="mb-5 h-9 w-auto select-none" draggable={false} />
          {step === "scan" ? (
            <>
              <h1 className="font-display text-[26px] font-bold tracking-tight">Checking this {platformName}</h1>
              <ol className="mt-6 flex flex-col gap-2.5 text-[13.5px]">
                {["Checking hardware", "Checking installed AI tools", "Checking local models"].map((l, i) => (
                  <li key={l} className="flex items-center gap-2.5">
                    {scan > i ? <Check className="h-4 w-4 text-emerald-600" /> : scan === i ? <Loader2 className="h-4 w-4 animate-spin text-accent" /> : <Circle className="h-4 w-4 text-muted/40" />}
                    <span className={scan < i ? "text-muted" : ""}>{l}…</span>
                  </li>
                ))}
              </ol>
              {error && <div className="mt-4 text-[12.5px] text-danger">{error}</div>}
            </>
          ) : step === "permissions" ? (
            <>
              <Steps steps={steps} current={current} />
              <PermissionsStep onDone={finish} />
            </>
          ) : plan && (
            <>
              <Steps steps={steps} current={current} />

              {step === "transcription" && (
                <>
                  <h1 className="font-display text-[26px] font-bold tracking-tight">{returning ? "The transcription model is missing" : "Transcription"}</h1>
                  <p className="mt-1 text-[13px] text-muted">
                    {ok(transcription)
                      ? "Every meeting is turned into text on this computer. This model does that; you can switch under Settings → Models."
                      : "Every meeting is turned into text on this computer. Pick the model for it; the recommended one fits this hardware."}
                  </p>
                  <HardwareCard hw={plan.hardware} className="mt-4" />
                  {ok(transcription) ? (
                    <div className="mt-3 panel overflow-hidden">
                      {transcription && <ResolutionRow r={transcription} progress={progressOf(transcription.download?.id)} />}
                    </div>
                  ) : (
                    <div className="mt-3 panel overflow-hidden">
                      {options.map((c) => <CandidateRow key={c.id} c={c} selected={chosen?.id === c.id} disabled={!!downloading} progress={progressOf(c.id)} onPick={() => setChoice(c.id)} />)}
                      {options.length === 0 && transcription && <ResolutionRow r={transcription} progress={progressOf(transcription.download?.id)} />}
                    </div>
                  )}
                  <div className="mt-6 flex items-center justify-end gap-2">
                    {ok(transcription) ? (
                      <Button variant="primary" onClick={() => (returning ? finish() : setStep("speakers"))}>Continue</Button>
                    ) : chosen ? (
                      <Button variant="primary" loading={!!downloading} onClick={() => download(chosen.id)}>
                        <Download className="h-3.5 w-3.5" /> Download {fmtBytes(chosen.sizeBytes)}
                      </Button>
                    ) : transcription?.download ? (
                      <Button variant="primary" loading={downloading === transcription.download.id} onClick={() => download(transcription.download!.id)}>
                        <Download className="h-3.5 w-3.5" /> Download {fmtBytes(transcription.download.sizeBytes)}
                      </Button>
                    ) : (
                      <Button variant="primary" onClick={() => (returning ? finish() : setStep("speakers"))}>Continue</Button>
                    )}
                  </div>
                </>
              )}

              {step === "speakers" && (
                <>
                  <h1 className="font-display text-[26px] font-bold tracking-tight">Speaker detection</h1>
                  <p className="mt-1 text-[13px] text-muted">
                    Huddle works out who is speaking and tags every line with its speaker. You can name, rename and merge speakers afterwards.
                  </p>
                  <div className="mt-4 panel flex items-center justify-between gap-4 px-4 py-3">
                    <div className="min-w-0">
                      <div className="text-[13.5px] font-medium">Separate speakers</div>
                      <div className="text-[12px] text-muted">{diarization ? "Each line is tagged with who said it." : "The transcript is one continuous text."}</div>
                    </div>
                    <Switch checked={diarization} onChange={(v) => { setDiarization(v); api.updateSettings({ "speakers.diarization": v }).catch(() => {}); }} />
                  </div>
                  <div className="mt-6 flex items-center justify-end gap-2">
                    <Button variant="ghost" onClick={() => setStep("transcription")}>Back</Button>
                    <Button variant="primary" onClick={() => setStep("ai")}>Continue</Button>
                  </div>
                </>
              )}

              {step === "ai" && (
                <>
                  <h1 className="font-display text-[26px] font-bold tracking-tight">AI notes <span className="text-muted">(optional)</span></h1>
                  <p className="mt-1 text-[13px] text-muted">
                    A local AI model writes the summary, topics, decisions and action items, and answers questions about your meetings.
                  </p>
                  {llm?.status === "unsupported" ? (
                    <div className="mt-4 panel px-4 py-3">
                      <div className="flex items-center gap-2 text-[13.5px] font-medium"><XCircle className="h-4 w-4 shrink-0 text-danger" /> Not possible on this {platformName}</div>
                      <div className="mt-1 text-[12.5px] text-muted">{llm.reason}</div>
                      <div className="mt-1 text-[12.5px] text-muted">Meetings still get their transcript with speakers.</div>
                    </div>
                  ) : (
                    <div className="mt-4 panel overflow-hidden">
                      {llm && <ResolutionRow r={llm} progress={progressOf(llm.download?.id)} />}
                    </div>
                  )}
                  {llm?.status === "download_required" && (
                    <p className="mt-3 text-[12.5px] text-muted">You can also download one later under Settings → Models.</p>
                  )}
                  <div className="mt-6 flex items-center justify-end gap-2">
                    <Button variant="ghost" onClick={() => setStep("speakers")}>Back</Button>
                    {ok(llm) || !llm?.download ? (
                      <Button variant="primary" onClick={afterAi}>Continue</Button>
                    ) : (
                      <>
                        {!downloading && <Button variant="ghost" onClick={afterAi}>Skip for now</Button>}
                        <Button variant="primary" loading={downloading === llm.download.id} onClick={() => download(llm.download!.id)}>
                          <Download className="h-3.5 w-3.5" /> Download {fmtBytes(llm.download.sizeBytes)}
                        </Button>
                      </>
                    )}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Steps({ steps, current }: { steps: { id: Step; label: string }[]; current: number }) {
  if (steps.length < 2) return null;
  return (
    <div className="mb-5 flex items-center gap-2 text-[12px] text-muted">
      {steps.map((s, i) => (
        <div key={s.id} className="flex items-center gap-2">
          {i > 0 && <span className="h-px w-5 bg-border" />}
          <span className={cn("flex h-5 w-5 items-center justify-center rounded-full border text-[11px] font-medium", i < current ? "border-emerald-600 text-emerald-600" : i === current ? "border-accent text-accent" : "border-border")}>
            {i < current ? <Check className="h-3 w-3" /> : i + 1}
          </span>
          <span className={cn(i === current && "font-medium text-fg")}>{s.label}</span>
        </div>
      ))}
    </div>
  );
}

function PermissionsStep({ onDone }: { onDone: () => void }) {
  const perms = usePermissions();
  const ok = allGranted(perms);
  // Raise the one-time system-audio prompt here, while the user is watching, and take its answer.
  useEffect(() => {
    native.requestSystemAudioPermission().then((system) => perms.setState((s) => ({ ...s, system }))).catch(() => {});
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <>
      <h1 className="font-display text-[26px] font-bold tracking-tight">Allow recording</h1>
      <p className="mt-1 text-[13px] text-muted">macOS asks once for each.</p>
      <div className="mt-5"><PermissionsPanel perms={perms} /></div>
      <div className="mt-6 flex items-center justify-end gap-2">
        {!ok && <Button variant="ghost" onClick={onDone}>Later</Button>}
        <Button variant="primary" onClick={onDone}>{ok ? "Start using Huddle" : "Continue"}</Button>
      </div>
    </>
  );
}

/** One transcription model to choose from: a radio row with the marketplace facts. */
function CandidateRow({ c, selected, disabled, progress, onPick }: { c: DownloadCandidate; selected: boolean; disabled: boolean; progress?: DownloadProgress; onPick: () => void }) {
  const pct = progress && progress.totalBytes ? Math.round((progress.receivedBytes / progress.totalBytes) * 100) : 0;
  return (
    <button type="button" disabled={disabled} onClick={onPick} className={cn("flex w-full items-start gap-3 border-b border-border px-4 py-3 text-left last:border-b-0 transition-colors", selected ? "bg-accent/5" : "hover:bg-fg/[0.03]", disabled && !selected && "opacity-50")}>
      <span className={cn("mt-[3px] flex h-4 w-4 shrink-0 items-center justify-center rounded-full border", selected ? "border-accent" : "border-fg/30")}>
        {selected && <span className="h-2 w-2 rounded-full bg-accent" />}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2 text-[13.5px] font-medium">
          {c.name}
          {c.recommended && <span className="rounded-full bg-accent/10 px-1.5 py-px text-[10.5px] font-semibold uppercase tracking-wide text-accent">Recommended</span>}
        </span>
        <span className="block text-[12px] text-muted">{c.purpose}</span>
        {c.fit === "slow" && c.fitReason && <span className="block text-[12px] text-amber-600 dark:text-amber-400">{c.fitReason}</span>}
        {progress && progress.state === "downloading" && (
          <>
            <span className="block text-[12px] text-muted">{fmtBytes(progress.receivedBytes)} of {fmtBytes(progress.totalBytes)} · {pct}%</span>
            <span className="mt-1.5 block h-1 overflow-hidden rounded bg-fg/10"><span className="block h-full bg-accent transition-all" style={{ width: `${pct}%` }} /></span>
          </>
        )}
        {progress && progress.state === "verifying" && <span className="block text-[12px] text-muted">Verifying checksum…</span>}
        {progress && progress.state === "failed" && <span className="block text-[12px] text-danger">{progress.error}</span>}
      </span>
      <span className="text-[12px] text-muted">{fmtBytes(c.sizeBytes)}</span>
    </button>
  );
}

function ResolutionRow({ r, progress }: { r: Resolution; progress?: DownloadProgress }) {
  const ok = r.status === "ready" || r.status === "builtin";
  const unsupported = r.status === "unsupported";
  const pct = progress && progress.totalBytes ? Math.round((progress.receivedBytes / progress.totalBytes) * 100) : 0;
  return (
    <div className="flex items-start gap-3 px-4 py-3">
      <div className="mt-[2px]">{ok || progress?.state === "done" ? <Check className="h-4 w-4 text-emerald-600" /> : unsupported ? <XCircle className="h-4 w-4 text-danger" /> : progress ? <Loader2 className="h-4 w-4 animate-spin text-accent" /> : <Download className="h-4 w-4 text-muted" />}</div>
      <div className="min-w-0 flex-1">
        <div className="font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">{TASK_LABEL[r.task] ?? r.task}</div>
        <div className="text-[13.5px] font-medium">{r.model?.name ?? r.download?.name ?? (r.status === "builtin" ? "Built in" : unsupported ? `Not possible on this ${platformName}` : "—")}</div>
        <div className="text-[12px] text-muted">{progress ? (progress.state === "downloading" ? `${fmtBytes(progress.receivedBytes)} of ${fmtBytes(progress.totalBytes)} · ${pct}%` : progress.state === "verifying" ? "Verifying checksum…" : progress.state === "failed" ? progress.error : "Installed") : r.reason}</div>
        {progress && progress.state === "downloading" && <div className="mt-1.5 h-1 overflow-hidden rounded bg-fg/10"><div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} /></div>}
      </div>
      <div className="text-[12px] text-muted">{ok ? "" : r.download ? fmtBytes(r.download.sizeBytes) : ""}</div>
    </div>
  );
}
