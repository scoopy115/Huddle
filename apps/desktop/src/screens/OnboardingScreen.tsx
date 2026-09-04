import { useEffect, useState } from "react";
import { Check, Circle, Download, HardDrive, Languages, Loader2 } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { DownloadProgress, Resolution, SetupPlan } from "@/types/engine";
import { fmtBytes, languageName } from "@/lib/format";
import { languageOptions, systemLanguage } from "@/lib/languages";
import { native } from "@/lib/native";
import { PermissionsPanel, allGranted, usePermissions } from "@/components/PermissionsPanel";
import { Button, Select } from "@/components/ui";
import logo from "@/assets/huddle-logo.svg";

const TASK_LABEL: Record<string, string> = { transcription: "Transcription", diarization: "Speaker detection", llm: "Meeting summaries" };

/** `returning`: models went missing after onboarding (or were skipped); the permissions step is left out. */
export function OnboardingScreen({ onDone, returning = false }: { onDone: () => void; returning?: boolean }) {
  const [step, setStep] = useState(0);
  const [plan, setPlan] = useState<SetupPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloads, setDownloads] = useState<DownloadProgress[]>([]);
  const [downloading, setDownloading] = useState(false);
  const [notesLang, setNotesLang] = useState("auto");
  const [sysLang, setSysLang] = useState("en");
  useEffect(() => {
    api.getSettings().then((s) => setNotesLang((s["general.uiLanguage"] as string) || "auto")).catch(() => {});
    native.getLocalePrefs().then((p) => setSysLang(systemLanguage(p.locale))).catch(() => {});
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        for (let i = 1; i <= 3; i++) { await new Promise((r) => setTimeout(r, 450)); if (alive) setStep(i); }
        const p = await api.setupPlan();
        if (alive) { setPlan(p); setStep(4); }
      } catch (e) { if (alive) setError(errorMessage(e)); }
    })();
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (!downloading) return;
    const t = setInterval(async () => {
      const list = await api.downloads();
      setDownloads(list);
      if (list.length && list.every((d) => d.state === "done" || d.state === "failed" || d.state === "cancelled")) {
        setDownloading(false);
        setPlan(await api.setupPlan());
      }
    }, 700);
    return () => clearInterval(t);
  }, [downloading]);

  const needed = plan?.resolutions.filter((r) => r.status === "download_required" && r.download) ?? [];

  const startDownloads = async () => {
    setDownloading(true);
    for (const r of needed) await api.startDownload(r.download!.id);
  };

  const finish = async () => { await api.updateSettings({ "onboarding.completed": true }); onDone(); };
  // Step 5 asks macOS for the two recording permissions while the user is watching, so the
  // prompts never appear later from a menu-bar recording (a background prompt bounces the Dock).
  const toPermissions = () => { if (returning) { finish(); } else { setStep(5); } };

  return (
    <div className="flex h-full flex-col">
      <div data-tauri-drag-region className="titlebar-drag h-[38px] shrink-0" />
      <div className="flex flex-1 items-center justify-center px-8 pb-12">
        <div className="w-full max-w-[460px]">
          {step === 5 ? (
            <PermissionsStep onDone={finish} />
          ) : step < 4 ? (
            <>
              <img src={logo} alt="Huddle" className="mb-5 h-9 w-auto select-none" draggable={false} />
              <h1 className="font-display text-[26px] font-bold tracking-tight">Preparing local AI</h1>
              <p className="mt-1 text-[13px] text-muted">Huddle checks what is already on this Mac before downloading anything.</p>
              <ol className="mt-6 flex flex-col gap-2.5 text-[13.5px]">
                {["Checking hardware", "Checking existing AI providers", "Checking local models"].map((l, i) => (
                  <li key={l} className="flex items-center gap-2.5">
                    {step > i ? <Check className="h-4 w-4 text-emerald-600" /> : step === i ? <Loader2 className="h-4 w-4 animate-spin text-accent" /> : <Circle className="h-4 w-4 text-muted/40" />}
                    <span className={step < i ? "text-muted" : ""}>{l}…</span>
                  </li>
                ))}
              </ol>
              {error && <div className="mt-4 text-[12.5px] text-danger">{error}</div>}
            </>
          ) : plan && (
            <>
              <img src={logo} alt="Huddle" className="mb-5 h-9 w-auto select-none" draggable={false} />
              <h1 className="font-display text-[26px] font-bold tracking-tight">{needed.length ? (returning ? "A model is missing" : "Almost ready") : "Your Mac is ready"}</h1>
              <p className="mt-1 text-[13px] text-muted">
                {plan.hardware.appleSilicon ? `${plan.hardware.cpuBrand ?? "Apple Silicon"} · ${fmtBytes(plan.hardware.memoryBytes)} unified memory · Metal acceleration` : plan.hardware.cpuBrand}
              </p>

              <div className="mt-5 panel flex items-center justify-between gap-4 px-4 py-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 text-[13px]"><Languages className="h-3.5 w-3.5 text-muted" /> Notes language</div>
                  <div className="text-[12px] text-muted">Summaries, decisions and action items are written in this language, whatever language is spoken. You can change it later in Settings.</div>
                </div>
                <Select value={notesLang} onChange={(e) => { setNotesLang(e.target.value); api.updateSettings({ "general.uiLanguage": e.target.value }).catch(() => {}); }}>
                  <option value="auto">System default ({languageName(sysLang)})</option>
                  {languageOptions().map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
                </Select>
              </div>

              <div className="mt-3 panel overflow-hidden">
                {plan.resolutions.map((r) => <ResolutionRow key={r.task} r={r} progress={downloads.find((d) => d.id === r.download?.id)} />)}
              </div>

              <div className="mt-4 flex items-center gap-2 text-[13px]">
                <HardDrive className="h-4 w-4 text-muted" />
                <span className="text-muted">Additional storage required:</span>
                <span className="font-semibold">{plan.additionalBytes ? fmtBytes(plan.additionalBytes) : "0 MB"}</span>
              </div>


              <div className="mt-6 flex items-center justify-end gap-2">
                {needed.length > 0 && !downloading && (
                  <Button variant="ghost" onClick={toPermissions}>Skip for now</Button>
                )}
                {needed.length > 0 ? (
                  <Button variant="primary" loading={downloading} onClick={startDownloads}><Download className="h-3.5 w-3.5" /> Download {fmtBytes(plan.additionalBytes)}</Button>
                ) : (
                  <Button variant="primary" onClick={toPermissions}>Continue</Button>
                )}
              </div>
            </>
          )}
        </div>
      </div>
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
      <img src={logo} alt="Huddle" className="mb-5 h-9 w-auto select-none" draggable={false} />
      <h1 className="font-display text-[26px] font-bold tracking-tight">Allow recording</h1>
      <p className="mt-1 text-[13px] text-muted">macOS asks once for each. Nothing is recorded until you press Record, and audio never leaves this Mac.</p>
      <div className="mt-5"><PermissionsPanel perms={perms} /></div>
      <div className="mt-6 flex items-center justify-end gap-2">
        {!ok && <Button variant="ghost" onClick={onDone}>Later</Button>}
        <Button variant="primary" onClick={onDone}>{ok ? "Start using Huddle" : "Continue"}</Button>
      </div>
    </>
  );
}

function ResolutionRow({ r, progress }: { r: Resolution; progress?: DownloadProgress }) {
  const ok = r.status === "ready" || r.status === "builtin";
  const pct = progress && progress.totalBytes ? Math.round((progress.receivedBytes / progress.totalBytes) * 100) : 0;
  return (
    <div className="flex items-start gap-3 border-b border-border px-4 py-3 last:border-b-0">
      <div className="mt-[2px]">{ok || progress?.state === "done" ? <Check className="h-4 w-4 text-emerald-600" /> : progress ? <Loader2 className="h-4 w-4 animate-spin text-accent" /> : <Download className="h-4 w-4 text-muted" />}</div>
      <div className="min-w-0 flex-1">
        <div className="font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">{TASK_LABEL[r.task] ?? r.task}</div>
        <div className="text-[13.5px] font-medium">{r.model?.name ?? r.download?.name ?? (r.status === "builtin" ? "Built in" : "—")}</div>
        <div className="text-[12px] text-muted">{progress ? (progress.state === "downloading" ? `${fmtBytes(progress.receivedBytes)} of ${fmtBytes(progress.totalBytes)} · ${pct}%` : progress.state === "verifying" ? "Verifying checksum…" : progress.state === "failed" ? progress.error : "Installed") : r.reason}</div>
        
        {progress && progress.state === "downloading" && <div className="mt-1.5 h-1 overflow-hidden rounded bg-fg/10"><div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} /></div>}
      </div>
      <div className="text-[12px] text-muted">{ok ? "0 MB" : r.download ? fmtBytes(r.download.sizeBytes) : ""}</div>
    </div>
  );
}
