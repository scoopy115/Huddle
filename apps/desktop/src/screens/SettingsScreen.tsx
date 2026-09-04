import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { open as openDialog } from "@tauri-apps/plugin-dialog";
import { Check, Cpu, Download, FolderOpen, HardDrive, Mic, RefreshCw, Sparkles, Star, Trash2, Users, Zap } from "lucide-react";
import { applyAppearance, type Appearance } from "@/lib/theme";
import { setSoundsEnabled } from "@/lib/sounds";
import { syncShellPrefs } from "@/lib/shellPrefs";
import { checkForUpdates, useUpdates } from "@/lib/updates";
import { api, errorMessage } from "@/lib/api";
import { native, type AppInfo, type EngineStatus, type InputDevice } from "@/lib/native";
import { PermissionsPanel, usePermissions } from "@/components/PermissionsPanel";
import { useNav } from "@/lib/nav";
import type { DownloadCandidate, DownloadProgress, Environment, KnownSpeaker, LocalModel, Resolution, StorageInfo, UserSettings } from "@/types/engine";
import { fmtBytes, languageName } from "@/lib/format";
import { languageOptions, systemLanguage } from "@/lib/languages";
import { cn, modKey } from "@/lib/utils";
import { Badge, Button, Card, DangerDialog, Dialog, InfoTip, Row, Select, Switch } from "@/components/ui";
import { McpSection } from "@/screens/settings/McpSection";

const SECTIONS = [
  { id: "general", label: "General" },
  { id: "recording", label: "Recording" },
  { id: "models", label: "Models" },
  { id: "speakers", label: "Speakers" },
  { id: "privacy", label: "Privacy" },
  { id: "mcp", label: "MCP" },
  { id: "advanced", label: "Advanced" },
];

type Update = (p: Partial<UserSettings>) => Promise<void>;

export function SettingsScreen({ section, engine }: { section?: string; engine: EngineStatus }) {
  const [active, setActive] = useState(section ?? "general");
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [env, setEnv] = useState<Environment | null>(null);
  const [resolutions, setResolutions] = useState<Resolution[]>([]);
  const [error, setError] = useState<string | null>(null);

  const { ai } = useNav();
  const load = useCallback(async () => {
    try {
      const [s, e, plan] = await Promise.all([api.getSettings(), api.environment(), api.setupPlan()]);
      setSettings(s); setEnv(e); setResolutions(plan.resolutions); setError(null);
      ai.refresh();
    } catch (e) { setError(errorMessage(e)); }
    // oxlint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => { load(); }, [load]);

  const update: Update = async (patch) => {
    try {
      setSettings(await api.updateSettings(patch));
      syncShellPrefs(patch);
      const plan = await api.setupPlan();
      setResolutions(plan.resolutions);
    } catch (e) { setError(errorMessage(e)); }
  };

  return (
    <div className="flex h-full">
      <nav className="w-[180px] shrink-0 border-r border-border bg-sidebar/60">
        <div data-tauri-drag-region className="titlebar-drag h-[52px] flex items-center px-4"><span data-tauri-drag-region className="page-title">Settings</span></div>
        <ul className="px-2">
          {SECTIONS.map((s) => (
            <li key={s.id}>
              <button onClick={() => setActive(s.id)} className={cn("relative w-full rounded-lg px-2.5 py-[6px] text-left text-[13px] transition-colors", active === s.id ? "bg-surface font-medium text-fg shadow-[0_1px_2px_rgb(28_25_24/0.06)] dark:bg-fg/[0.07] dark:shadow-none" : "text-fg/70 hover:bg-fg/[0.05]")}>
                <span className={cn("absolute -left-2 top-1/2 h-[14px] w-[3px] -translate-y-1/2 rounded-full bg-accent", active === s.id ? "opacity-100" : "opacity-0")} />
                {s.label}
              </button>
            </li>
          ))}
        </ul>
      </nav>
      <div className="flex-1 overflow-y-auto">
        <div data-tauri-drag-region className="titlebar-drag h-[52px]" />
        <div className="mx-auto max-w-[680px] px-6 pb-10">
          <h2 className="mb-4 font-display text-[22px] font-bold tracking-tight">{SECTIONS.find((s) => s.id === active)?.label}</h2>
          {error && <div className="mb-3 text-[12.5px] text-danger">{error}</div>}
          {settings && env && (
            <>
              {active === "general" && <General settings={settings} update={update} />}
              {active === "recording" && <Recording settings={settings} update={update} />}
              {active === "models" && <Models settings={settings} env={env} resolutions={resolutions} update={update} reload={load} />}
              {active === "speakers" && <Speakers settings={settings} update={update} />}
              {active === "privacy" && <Privacy settings={settings} update={update} />}
              {active === "mcp" && <McpSection settings={settings} update={update} />}
              {active === "advanced" && <Advanced settings={settings} env={env} engine={engine} update={update} resolutions={resolutions} />}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ---- helpers ------------------------------------------------------------------------------------

const GB = 1024 ** 3;
const autoLabel = (pick: string | undefined) => (pick ? `Automatic (${pick})` : "Automatic");
function sourceLabel(s: string) {
  return ({ our_app: "Huddle", ollama: "Ollama", lm_studio: "LM Studio", huggingface: "Hugging Face cache", whisper_cpp: "whisper.cpp", whisperkit: "WhisperKit", mlx: "MLX", custom: "Custom" } as Record<string, string>)[s] ?? s;
}
/** "Whisper large-v3-turbo (Apple Silicon)" / "(CPU)": the runtime is part of the name, as in the marketplace. */
const modelTitle = (m: LocalModel) => {
  if (m.task !== "transcription" || !m.meta.whisperSize) return m.name;
  const runtime = m.format === "MLX" ? "Apple Silicon" : m.format === "CTranslate2" ? "CPU" : m.format;
  return `Whisper ${m.meta.whisperSize}${runtime ? ` (${runtime})` : ""}`;
};

function ComputeSelect({ env, value, onChange }: { env: Environment; value: string; onChange: (v: string) => void }) {
  const rec = env.devices.find((d) => d.recommended && d.available);
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="auto">{autoLabel(rec ? `${rec.name} — ${rec.backend === "metal" ? "Metal" : rec.backend.toUpperCase()}` : undefined)}</option>
      {env.devices.filter((d) => d.available).map((d) => <option key={d.id} value={d.id}>{d.name} — {d.backend === "metal" ? "Metal" : d.backend.toUpperCase()}</option>)}
    </Select>
  );
}

function useSystemDark() {
  const mq = window.matchMedia("(prefers-color-scheme: dark)");
  const [dark, setDark] = useState(mq.matches);
  useEffect(() => { const h = (e: MediaQueryListEvent) => setDark(e.matches); mq.addEventListener("change", h); return () => mq.removeEventListener("change", h); }, [mq]);
  return dark;
}

// ---- General --------------------------------------------------------------------------------------

function General({ settings, update }: { settings: UserSettings; update: Update }) {
  const [storage, setStorage] = useState<StorageInfo | null>(null);
  useEffect(() => { api.storage().then(setStorage).catch(() => {}); }, [settings]);
  const gb = Math.round((settings["storage.maxBytes"] || 10 * GB) / GB);
  const [pending, setPending] = useState(gb);
  useEffect(() => setPending(gb), [gb]);
  const systemDark = useSystemDark();
  const [sysLang, setSysLang] = useState("en");
  useEffect(() => { native.getLocalePrefs().then((p) => setSysLang(systemLanguage(p.locale))).catch(() => {}); }, []);
  return (
    <>
      <h3 className="mb-2 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">Visibility and sounds</h3>
      <Card>
        <Row label="Appearance">
          <Select value={(settings["general.appearance"] as string) ?? "system"} onChange={(e) => { const a = e.target.value as Appearance; applyAppearance(a); update({ "general.appearance": a } as Partial<UserSettings>); }}>
            <option value="system">System default ({systemDark ? "Dark" : "Light"})</option><option value="light">Light</option><option value="dark">Dark</option>
          </Select>
        </Row>
        <Row label="Interface sounds" info="Soft taps and chimes for buttons, recording start and stop, and finished meetings.">
          <Switch checked={settings["general.sounds"] !== false} onChange={(v) => { setSoundsEnabled(v); update({ "general.sounds": v }); }} />
        </Row>
      </Card>

      <h3 className="mb-2 mt-6 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">Menu bar</h3>
      <Card>
        <Row label="Keep Huddle in the menu bar" hint={`Closing the window keeps a small recorder in the menu bar. ${modKey}⌥R starts or stops a recording from anywhere.`}>
          <Switch checked={!!settings["general.menuBar"]} onChange={(v) => update({ "general.menuBar": v })} />
        </Row>
      </Card>

      <h3 className="mb-2 mt-6 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">Updates</h3>
      <Card>
        <Row label="Check for updates automatically">
          <Switch checked={settings["general.autoUpdate"] !== false} onChange={(v) => update({ "general.autoUpdate": v })} />
        </Row>
        <UpdateRow />
      </Card>

      <h3 className="mb-2 mt-6 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">Notes</h3>
      <Card>
        <Row label="Notes language" info="Summaries, decisions, action items and answers are written in this language. Spoken languages are always detected automatically.">
          <Select value={settings["general.uiLanguage"] ?? "auto"} onChange={(e) => update({ "general.uiLanguage": e.target.value })}>
            <option value="auto">System default ({languageName(sysLang)})</option>
            {languageOptions().map((l) => <option key={l.code} value={l.code}>{l.name}</option>)}
          </Select>
        </Row>
        <Row label="Find action items automatically" info="Off: action items are only extracted when you press “Find action items” on a meeting.">
          <Switch checked={!!settings["notes.autoActionItems"]} onChange={(v) => update({ "notes.autoActionItems": v })} />
        </Row>
      </Card>

      <h3 className="mb-2 mt-6 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">Storage</h3>
      <Card>
        <div className="border-b border-border px-4 py-3">
          <div className="flex items-center justify-between text-[13px]">
            <span className="inline-flex items-center gap-1.5"><HardDrive className="h-3.5 w-3.5 text-muted" /> Recordings limit</span>
            <span className="font-medium tabular-nums">{pending} GB</span>
          </div>
          <input type="range" min={5} max={50} step={1} value={pending} onChange={(e) => setPending(Number(e.target.value))}
            onMouseUp={() => update({ "storage.maxBytes": pending * GB })} onKeyUp={() => update({ "storage.maxBytes": pending * GB })}
            className="mt-2 w-full accent-[rgb(var(--accent))]" />
          <div className="mt-1 flex justify-between text-[11px] text-muted"><span>5 GB</span><span>50 GB</span></div>
          {storage && (
            <div className="mt-2">
              <div className="h-1.5 overflow-hidden rounded bg-fg/10"><div className="h-full bg-accent" style={{ width: `${Math.min(100, (storage.recordingsBytes / (storage.maxBytes || 1)) * 100)}%` }} /></div>
              <div className="mt-1 text-[12px] text-muted">{fmtBytes(storage.recordingsBytes)} used by {storage.meetingCount} meeting{storage.meetingCount === 1 ? "" : "s"}. When the limit is reached, the oldest audio is removed first. Transcripts, summaries and action items are always kept.</div>
            </div>
          )}
        </div>
        <Row label="Storage location" hint={storage?.dataDir ?? ""}>
          <Button size="sm" onClick={() => storage && native.revealInFinder(storage.dataDir).catch(() => {})}><FolderOpen className="h-3.5 w-3.5" /> Show in Finder</Button>
        </Row>
      </Card>
    </>
  );
}

// ---- Recording ------------------------------------------------------------------------------------

function Recording({ settings, update }: { settings: UserSettings; update: Update }) {
  const [devices, setDevices] = useState<InputDevice[]>([]);
  const perms = usePermissions();
  useEffect(() => { native.listInputDevices().then(setDevices).catch(() => {}); }, []);
  const mics = devices.filter((d) => !d.isLoopback);
  const def = mics.find((d) => d.isDefault);
  return (
    <>
      <Card>
        <Row label="Microphone">
          <Select value={settings["recording.inputDevice"] ?? ""} onChange={(e) => update({ "recording.inputDevice": e.target.value || null })}>
            <option value="">{autoLabel(def?.name)}</option>
            {mics.map((d) => <option key={d.id} value={d.name}>{d.name}</option>)}
          </Select>
        </Row>
        <Row label="System audio">
          <Select value={settings["recording.systemAudio"] ? "all" : "off"} onChange={(e) => update({ "recording.systemAudio": e.target.value === "all" })}>
            <option value="off">Off</option>
            <option value="all">All apps (online meetings, calls)</option>
          </Select>
        </Row>
        <Row label="Format"><span className="text-[12.5px] text-muted">WAV, 16-bit, mono</span></Row>
      </Card>
      <h3 className="mb-2 mt-6 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">Permissions</h3>
      <PermissionsPanel perms={perms} compact />
    </>
  );
}

// ---- Models ---------------------------------------------------------------------------------------

function CandidateRow({ c, progress, installed, memoryBytes, onStart, onCancel }: { c: DownloadCandidate; progress?: DownloadProgress; installed: boolean; memoryBytes?: number | null; onStart: () => void; onCancel: () => void }) {
  const pct = progress && progress.totalBytes ? Math.round((progress.receivedBytes / progress.totalBytes) * 100) : 0;
  const active = progress && (progress.state === "downloading" || progress.state === "verifying");
  // Greyed out when this Mac has less memory than the model needs; the row stays visible so the option is known.
  const tooBig = !!c.minMemoryBytes && !!memoryBytes && memoryBytes < c.minMemoryBytes;
  return (
    <div className={cn("flex items-start gap-3 border-b border-border px-4 py-3 last:border-b-0", tooBig && !installed && "opacity-50")}>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[13px] font-medium">
          {c.name}
          {c.recommended && <Badge tone="accent"><Star className="h-3 w-3" /> Recommended</Badge>}
        </div>
        <div className="mt-0.5 flex flex-wrap items-center gap-x-3 text-[12px] text-muted">
          <span className="inline-flex items-center gap-1"><Sparkles className="h-3 w-3" />{c.purpose}</span>
          <span className="inline-flex items-center gap-1"><Download className="h-3 w-3" />{c.sizeBytes ? fmtBytes(c.sizeBytes) : "size varies"}</span>
        </div>
        {c.description && <div className="mt-0.5 text-[12px] text-muted">{c.description}</div>}
        {tooBig && !installed && <div className="mt-0.5 text-[12px] text-danger">Needs {fmtBytes(c.minMemoryBytes!)} of memory; this Mac has {fmtBytes(memoryBytes!)}.</div>}
        {active && <div className="mt-1.5 h-1 w-full overflow-hidden rounded bg-fg/10"><div className="h-full bg-accent transition-all" style={{ width: `${pct}%` }} /></div>}
        {progress?.state === "failed" && <div className="mt-1 text-[12px] text-danger">{progress.error}</div>}
      </div>
      <div className="shrink-0">
        {installed ? <Badge tone="good"><Check className="h-3 w-3" /> Installed</Badge>
          : active ? <div className="flex items-center gap-2 text-[12px] text-muted">{progress.state === "verifying" ? "Finishing…" : `${pct}%`}<Button size="sm" variant="ghost" onClick={onCancel}>Cancel</Button></div>
          : <Button size="sm" disabled={tooBig} title={tooBig ? "Not enough memory on this Mac" : undefined} onClick={onStart}><Download className="h-3.5 w-3.5" /> Download</Button>}
      </div>
    </div>
  );
}

const sourceOf = (m: LocalModel) => m.source === "ollama" ? (m.meta.pulledByHuddle ? "Installed by Huddle" : "Installed through Ollama")
  : m.source === "our_app" ? "Installed by Huddle" : m.source === "huggingface" ? "Found in Hugging Face cache" : sourceLabel(m.source);

/** One installed model with its radio button; defined at module level so React keeps its state. */
function ModelRow({ m, selectedKey, settings, update, onRemove, inUse }: { m: LocalModel; selectedKey: "models.whisper" | "models.ai"; settings: UserSettings; update: Update; onRemove: (m: LocalModel) => void; inUse: boolean }) {
  return (
    <div className="flex items-center gap-3 border-b border-border px-4 py-3 last:border-b-0">
      <input type="radio" name={selectedKey} checked={settings[selectedKey] === m.id} onChange={() => update({ [selectedKey]: m.id } as Partial<UserSettings>)} className="accent-[rgb(var(--accent))]" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[13px] font-medium">
          {modelTitle(m)}
          {m.meta.recommended && <Badge tone="accent"><Star className="h-3 w-3" /> Recommended</Badge>}
          {inUse && <Badge tone="good"><Check className="h-3 w-3" /> In use</Badge>}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 text-[12px] text-muted">
          <span className="inline-flex items-center gap-1"><Download className="h-3 w-3" />{sourceOf(m)}</span>
          {m.meta.parameterSize && <span className="inline-flex items-center gap-1"><Cpu className="h-3 w-3" />{m.meta.parameterSize} parameters</span>}
          {m.sizeBytes ? <span className="inline-flex items-center gap-1"><HardDrive className="h-3 w-3" />{fmtBytes(m.sizeBytes)}</span> : null}
        </div>
      </div>
      {(!m.externallyManaged || m.meta.pulledByHuddle) && (
        <Button size="sm" variant="ghost" title="Remove" onClick={() => onRemove(m)}><Trash2 className="h-3.5 w-3.5" /></Button>
      )}
    </div>
  );
}

function Tab({ id, label, tab, setTab }: { id: "transcript" | "summaries"; label: string; tab: "transcript" | "summaries"; setTab: (t: "transcript" | "summaries") => void }) {
  return (
    <button onClick={() => setTab(id)} className={cn("rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors", tab === id ? "bg-ink text-ink-fg" : "text-muted hover:text-fg")}>{label}</button>
  );
}

function Models({ settings, env, resolutions, update, reload }: { settings: UserSettings; env: Environment; resolutions: Resolution[]; update: Update; reload: () => Promise<void> }) {
  const [tab, setTab] = useState<"transcript" | "summaries">("transcript");
  const [cands, setCands] = useState<DownloadCandidate[]>([]);
  const [downloads, setDownloads] = useState<DownloadProgress[]>([]);
  const [confirm, setConfirm] = useState<LocalModel | null>(null);
  const [scanning, setScanning] = useState(false);
  useEffect(() => { api.candidates().then(setCands).catch(() => {}); }, []);
  useEffect(() => {
    const t = setInterval(async () => {
      const d = await api.downloads().catch(() => []);
      setDownloads((prev) => { if (prev.some((p) => p.state === "downloading") && d.some((x) => x.state === "done")) reload(); return d; });
    }, 1000);
    return () => clearInterval(t);
  }, [reload]);

  const whisper = env.models.filter((m) => m.task === "transcription" && m.compatible);
  const ai = env.models.filter((m) => m.task === "llm" && m.source === "ollama" && m.compatible && m.meta.generalChat !== false);
  const aiSorted = useMemo(() => [...ai].sort((a, b) => Number(!!b.meta.recommended) - Number(!!a.meta.recommended) || a.name.localeCompare(b.name)), [ai]);
  // "In use" is what the resolver returns right now (it follows a manual pick immediately);
  // "Automatic (…)" names what Automatic would take, whatever is selected.
  const whisperRes = resolutions.find((r) => r.task === "transcription");
  const whisperAutoModel = whisperRes?.autoModel ?? whisperRes?.model;
  const whisperAuto = whisperAutoModel ? modelTitle(whisperAutoModel) : undefined;
  const aiRes = resolutions.find((r) => r.task === "llm");
  const inUseIds = new Set(resolutions.map((r) => r.model?.id).filter(Boolean));
  const ollama = env.providers.find((p) => p.id === "ollama");
  // "Installed" means the model is actually usable: it appears in the inventory as a Huddle-managed
  // model. A snapshot folder alone is not enough — it exists from the first byte of a download.
  const isInstalled = (c: DownloadCandidate) => c.task === "llm"
    ? env.models.some((m) => m.source === "ollama" && m.name === c.url)
    : env.models.some((m) => m.task === "transcription" && m.compatible && m.source === "our_app" && m.id === `our_app:${c.url}`);
  const candsFor = (task: string) => [...cands.filter((c) => c.task === task)].sort((a, b) => Number(b.recommended) - Number(a.recommended));
  // Models Huddle pulled through Ollama are Huddle's (and removable); the rest belong to the user's Ollama.


  const recWhisper = env.models.find((m) => m.task === "transcription" && m.compatible && m.meta.recommended)
    ?? cands.find((c) => c.task === "transcription" && c.recommended);
  // When the automatically chosen model is itself in the recommended band, show that one.
  const recAi = (aiRes?.model && aiRes.model.meta.recommended ? aiRes.model : undefined)
    ?? aiSorted.find((m) => m.meta.recommended) ?? cands.find((c) => c.task === "llm" && c.recommended);
  const usedWhisper = whisperAutoModel && !settings["models.whisper"] ? whisperAutoModel : whisper.find((m) => m.id === settings["models.whisper"]) ?? whisperAutoModel;
  const usedAi = settings["models.ai"] ? ai.find((m) => m.id === settings["models.ai"]) ?? aiRes?.model : aiRes?.model;
  const nameOf = (x: LocalModel | DownloadCandidate | undefined) => !x ? "—" : "task" in x && "compatible" in x ? modelTitle(x as LocalModel) : (x as DownloadCandidate).name;
  const installedFlag = (x: LocalModel | DownloadCandidate | undefined) => !!x && "compatible" in x;


  return (
    <>
      <div className="mb-4 grid grid-cols-2 gap-3">
        <Card className="p-4">
          <div className="mb-2 flex items-center gap-1.5 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted"><Star className="h-3.5 w-3.5" /> Recommended for this Mac</div>
          <div className="text-[12px] text-muted">{env.hardware.cpuBrand} · {fmtBytes(env.hardware.memoryBytes)}</div>
          <div className="mt-2 flex items-start gap-2 text-[13px]"><Mic className="mt-[3px] h-3.5 w-3.5 text-muted" /><div><div className="font-medium">{nameOf(recWhisper)}</div><div className="text-[11.5px] text-muted">{installedFlag(recWhisper) ? "Installed" : "Available in the marketplace"}</div></div></div>
          <div className="mt-2 flex items-start gap-2 text-[13px]"><Sparkles className="mt-[3px] h-3.5 w-3.5 text-muted" /><div><div className="font-medium">{nameOf(recAi)}</div><div className="text-[11.5px] text-muted">{installedFlag(recAi) ? "Installed" : "Available in the marketplace"}</div></div></div>
        </Card>
        <Card className="p-4">
          <div className="mb-2 flex items-center gap-1.5 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted"><Zap className="h-3.5 w-3.5" /> Used for processing</div>
          <div className="text-[12px] text-muted">{settings["models.whisper"] || settings["models.ai"] ? "Your selection" : "Chosen automatically"}</div>
          <div className="mt-2 flex items-start gap-2 text-[13px]"><Mic className="mt-[3px] h-3.5 w-3.5 text-muted" /><div><div className="font-medium">{usedWhisper ? modelTitle(usedWhisper) : "No Whisper model"}</div><div className="text-[11.5px] text-muted">Transcript</div></div></div>
          <div className="mt-2 flex items-start gap-2 text-[13px]"><Sparkles className="mt-[3px] h-3.5 w-3.5 text-muted" /><div><div className="font-medium">{usedAi ? usedAi.name : "No AI model"}</div><div className="text-[11.5px] text-muted">Summary</div></div></div>
        </Card>
      </div>
      <Card className="mb-4">
        <Row label="Compute device" info="Where transcription runs. Metal uses the GPU of Apple Silicon Macs and is several times faster than the CPU.">
          <ComputeSelect env={env} value={settings["general.computeDevice"]} onChange={(v) => update({ "general.computeDevice": v })} />
        </Row>
      </Card>
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-1 panel p-1">
          <Tab id="transcript" label="Transcript" tab={tab} setTab={setTab} />
          <Tab id="summaries" label="Summaries" tab={tab} setTab={setTab} />
        </div>
        <Button size="sm" variant="ghost" loading={scanning || env.scanning} onClick={async () => { setScanning(true); try { await api.rescan(); await reload(); } finally { setScanning(false); } }}><RefreshCw className="h-3 w-3" /> Rescan</Button>
      </div>

      {tab === "transcript" && (
        <>
          <Card>
            <div className="flex items-center gap-3 border-b border-border px-4 py-3">
              <input type="radio" name="models.whisper" checked={!settings["models.whisper"]} onChange={() => update({ "models.whisper": null })} className="accent-[rgb(var(--accent))]" />
              <div className="text-[13px]">{autoLabel(whisperAuto)}</div>
            </div>
            {whisper.map((m) => <ModelRow key={m.id} m={m} selectedKey="models.whisper" settings={settings} update={update} onRemove={setConfirm} inUse={inUseIds.has(m.id)} />)}
            {whisper.length === 0 && <div className="px-4 py-3 text-[12.5px] text-muted">No Whisper model installed yet.</div>}
          </Card>
          <h3 className="mb-2 mt-6 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">Model marketplace</h3>
          <Card>
            {candsFor("transcription").map((c) => <CandidateRow key={c.id} c={c} installed={isInstalled(c)} memoryBytes={env.hardware.memoryBytes} progress={downloads.find((d) => d.id === c.id)} onStart={() => api.startDownload(c.id).then(() => api.downloads().then(setDownloads))} onCancel={() => api.cancelDownload(c.id)} />)}
          </Card>
        </>
      )}

      {tab === "summaries" && (
        <>
          {ollama?.status !== "available" && (
            <div className="mb-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[12.5px]">
              {ollama?.status === "installed_not_running" ? "Ollama is installed but not running. Start it to use or download AI models." : "Ollama is not installed. Get it from ollama.com — Huddle will then download a model for you."}
            </div>
          )}
          <Card>
            <div className="flex items-center gap-3 border-b border-border px-4 py-3">
              <input type="radio" name="models.ai" checked={!settings["models.ai"]} onChange={() => update({ "models.ai": null })} className="accent-[rgb(var(--accent))]" />
              <div className="flex items-center gap-1.5 text-[13px]">{autoLabel((aiRes?.autoModel ?? aiRes?.model)?.name)} <InfoTip text="A model of 4 to 9 billion parameters is all Huddle needs. Bigger models are slower without better notes." /></div>
            </div>
            {aiSorted.map((m) => <ModelRow key={m.id} m={m} selectedKey="models.ai" settings={settings} update={update} onRemove={setConfirm} inUse={inUseIds.has(m.id)} />)}
            {aiSorted.length === 0 && <div className="px-4 py-3 text-[12.5px] text-muted">No suitable AI model installed yet.</div>}
          </Card>
          <h3 className="mb-2 mt-6 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">Model marketplace</h3>
          <Card>
            {candsFor("llm").map((c) => <CandidateRow key={c.id} c={c} installed={isInstalled(c)} memoryBytes={env.hardware.memoryBytes} progress={downloads.find((d) => d.id === c.id)} onStart={() => api.startDownload(c.id).then(() => api.downloads().then(setDownloads))} onCancel={() => api.cancelDownload(c.id)} />)}
          </Card>
        </>
      )}

      <DangerDialog open={!!confirm} onClose={() => setConfirm(null)} title="Remove this model?" confirmLabel="Remove model" seconds={3}
        onConfirm={async () => { if (confirm) { await api.deleteModel(confirm.id); setConfirm(null); reload(); } }}>
        {confirm?.name}{confirm?.sizeBytes ? ` (${fmtBytes(confirm.sizeBytes)})` : ""} will be deleted{confirm?.source === "ollama" ? " from Ollama" : ""}. You can download it again later.
      </DangerDialog>
    </>
  );
}

// ---- Speakers -------------------------------------------------------------------------------------

function Speakers({ settings, update }: { settings: UserSettings; update: Update }) {
  const [known, setKnown] = useState<KnownSpeaker[]>([]);
  const [confirm, setConfirm] = useState<KnownSpeaker | null>(null);
  const load = () => api.knownSpeakers().then(setKnown).catch(() => {});
  useEffect(() => { load(); }, []);
  return (
    <>
      <Card>
        <Row label="Speaker detection"><Switch checked={settings["speakers.diarization"]} onChange={(v) => update({ "speakers.diarization": v })} /></Row>
        <Row label="Name speakers from the conversation" info='When someone is addressed by name ("Jack, can you please…") and answers, that speaker gets the name automatically. You can always change it.'>
          <Switch checked={settings["speakers.inferNames"]} onChange={(v) => update({ "speakers.inferNames": v })} />
        </Row>
        <Row label="Recognise voices" info="Voices you have named before are matched by their voice profile and suggested for confirmation.">
          <Switch checked={settings["speakers.recognition"]} onChange={(v) => update({ "speakers.recognition": v })} />
        </Row>
      </Card>
      <h3 className="mb-2 mt-6 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">Known voices</h3>
      <Card>
        {known.length === 0 && <div className="px-4 py-3 text-[12.5px] text-muted">No voices yet. Name a speaker in a meeting to remember their voice.</div>}
        {known.map((k) => (
          <Row key={k.id} label={k.name} hintNode={<span className="flex items-center gap-3"><span className="inline-flex items-center gap-1"><Users className="h-3 w-3" />{k.meetingCount} meeting{k.meetingCount === 1 ? "" : "s"}</span>{k.hasEmbedding && <span className="inline-flex items-center gap-1"><Mic className="h-3 w-3" />voice profile from {k.nSamples} recording{k.nSamples === 1 ? "" : "s"}</span>}</span>}>
            <Button size="sm" variant="ghost" title="Delete voice" onClick={() => setConfirm(k)}><Trash2 className="h-3.5 w-3.5" /></Button>
          </Row>
        ))}
      </Card>
      {/* A quick confirm, no countdown: the voice profile can be rebuilt by naming the speaker again. */}
      <DangerDialog open={!!confirm} onClose={() => setConfirm(null)} title="Delete this voice?" confirmLabel="Delete voice" seconds={0}
        onConfirm={async () => { if (confirm) { await api.deleteKnownSpeaker(confirm.id); setConfirm(null); load(); } }}>
        Huddle forgets the voice profile of “{confirm?.name}” and will no longer recognise this person automatically. Meetings and speaker names are kept.
      </DangerDialog>
    </>
  );
}

// ---- Privacy --------------------------------------------------------------------------------------

function Privacy({ settings, update }: { settings: UserSettings; update: Update }) {
  const [confirm, setConfirm] = useState<"meetings" | "embeddings" | null>(null);
  return (
    <>
      <Card>
        <Row label="Delete audio after" info="Audio older than this is removed. Transcripts and notes are kept.">
          <Select value={String(settings["privacy.retentionDays"])} onChange={(e) => update({ "privacy.retentionDays": Number(e.target.value) })}>
            <option value="0">Never</option><option value="7">7 days</option><option value="30">30 days</option><option value="90">90 days</option>
          </Select>
        </Row>
      </Card>
      <h3 className="mb-2 mt-6 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">Delete data</h3>
      <Card>
        <Row label="Delete known voice profiles" hint="Names stay; voice recognition starts from scratch."><Button size="sm" variant="danger" onClick={() => setConfirm("embeddings")}>Delete profiles</Button></Row>
        <Row label="Delete all meeting data" hint="Every recording, transcript, summary and action item."><Button size="sm" variant="danger" onClick={() => setConfirm("meetings")}>Delete everything</Button></Row>
      </Card>
      <DangerDialog open={!!confirm} onClose={() => setConfirm(null)} title={confirm === "meetings" ? "Delete all meeting data?" : "Delete voice profiles?"}
        confirmLabel={confirm === "meetings" ? "Delete everything" : "Delete profiles"}
        onConfirm={async () => { if (confirm === "meetings") await api.deleteAllMeetings(); else await api.deleteSpeakerEmbeddings(); setConfirm(null); }}>
        {confirm === "meetings" ? "This permanently removes all meetings, recordings, transcripts, summaries and action items from this Mac. There is no undo." : "All stored voice embeddings will be removed permanently. Huddle will no longer recognise known voices until you name them again."}
      </DangerDialog>
    </>
  );
}

function UpdateRow() {
  const u = useUpdates();
  const [touched, setTouched] = useState(false);
  const status = u.checking ? "Checking…"
    : !touched ? ""
    : u.error ? `Could not check: ${u.error}`
    : u.available ? `Huddle ${u.available.version} is available.`
    : u.checkedAt ? `You have the latest version${u.currentVersion ? ` (${u.currentVersion})` : ""}.` : "";
  return (
    <Row label="Update Huddle" hint={status}>
      <Button size="sm" loading={u.checking} onClick={() => { setTouched(true); checkForUpdates({ manual: true }); }}><RefreshCw className="h-3.5 w-3.5" /> Check for updates</Button>
    </Row>
  );
}

// ---- Advanced -------------------------------------------------------------------------------------

function Advanced({ settings, env, engine, update, resolutions }: { settings: UserSettings; env: Environment; engine: EngineStatus; update: Update; resolutions: Resolution[] }) {
  const [storage, setStorage] = useState<StorageInfo | null>(null);
  const [move, setMove] = useState<{ kind: "models" | "logs"; path: string } | null>(null);
  const dev = settings["developer.mode"];
  const reloadStorage = () => api.storage().then(setStorage).catch(() => {});
  useEffect(() => { reloadStorage(); }, []);
  const [info, setInfo] = useState<AppInfo | null>(null);
  useEffect(() => { native.appInfo().then(setInfo).catch(() => {}); }, []);
  const ollama = env.providers.find((p) => p.id === "ollama");
  const aiRes = resolutions.find((r) => r.task === "llm");

  const pick = async (kind: "models" | "logs") => {
    const picked = await openDialog({ directory: true, multiple: false, defaultPath: kind === "models" ? storage?.modelsDir : storage?.logsDir });
    if (!picked) return;
    setMove({ kind, path: typeof picked === "string" ? picked : (picked as { path: string }).path });
  };

  return (
    <>
      <Card>
        <Row label="Model folder" hint={storage?.modelsDir ?? ""}>
          <Button size="sm" onClick={() => pick("models")}><FolderOpen className="h-3.5 w-3.5" /> Change…</Button>
        </Row>
        <Row label="Logs folder" hint={storage?.logsDir ?? ""}>
          <Button size="sm" onClick={() => pick("logs")}><FolderOpen className="h-3.5 w-3.5" /> Change…</Button>
        </Row>
        <Row label="Developer mode">
          <Switch checked={dev} onChange={(v) => update({ "developer.mode": v })} />
        </Row>
      </Card>

      <h3 className="mb-2 mt-6 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">About</h3>
      <Card>
        <Row label="Huddle version" hint={info?.bundlePath ?? ""}>
          <span className="font-mono text-[12px] text-fg/80">{info ? `${info.version} (build ${info.build})` : "…"}</span>
        </Row>
      </Card>

      {dev && (
        <>
          <h3 className="mb-2 mt-6 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">Developer</h3>
          <Card>
            <Row label="Processing engine" hint={engine.port ? `127.0.0.1:${engine.port}` : ""}>
              <Badge tone={engine.state === "ready" ? "good" : "bad"}>{engine.state}</Badge>
              <Button size="sm" onClick={() => native.engineRestart()}>Restart</Button>
            </Row>
            <Row label="AI provider" hint={`Ollama${aiRes?.model ? ` · ${aiRes.model.name}` : ""}`}>
              <Badge tone={ollama?.status === "available" ? "good" : "bad"}>{ollama?.status === "available" ? "running" : (ollama?.status ?? "unknown").replace(/_/g, " ")}</Badge>
              <Button size="sm" onClick={async () => { await api.rescan(); }}>Reconnect</Button>
            </Row>
          </Card>
          <h3 className="mb-2 mt-6 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">Processing diagnostics</h3>
          <LogView />
        </>
      )}

      <Dialog open={!!move} onClose={() => setMove(null)} title={`Move ${move?.kind === "models" ? "model" : "logs"} folder`}
        footer={<>
          <Button onClick={() => setMove(null)}>Cancel</Button>
          <Button onClick={async () => { if (move) { await api.moveDir(move.kind, move.path, false); setMove(null); reloadStorage(); } }}>Use empty folder</Button>
          <Button variant="primary" onClick={async () => { if (move) { await api.moveDir(move.kind, move.path, true); setMove(null); reloadStorage(); } }}>Move files</Button>
        </>}>
        <p>New location:</p>
        <p className="selectable mt-1 break-all font-mono text-[12px]">{move?.path}</p>
        <p className="mt-3 text-muted">Do you want to move the existing files from the current folder to the new one?</p>
      </Dialog>
    </>
  );
}


/** Engine log that refreshes every second and follows the tail — unless the user has
 * scrolled up, in which case it stays put until they scroll back to the bottom. */
function LogView() {
  const [log, setLog] = useState("");
  const box = useRef<HTMLPreElement>(null);
  const follow = useRef(true);
  useEffect(() => {
    const tick = () => api.engineLog().then((l) => setLog(typeof l === "string" ? l : "")).catch(() => {});
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => { if (follow.current && box.current) box.current.scrollTop = box.current.scrollHeight; }, [log]);
  return (
    <pre ref={box} onScroll={(e) => { const el = e.currentTarget; follow.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24; }}
      className="selectable max-h-[360px] overflow-auto panel p-3 font-mono text-[10.5px] leading-snug text-fg/75">{log || "No engine log yet."}</pre>
  );
}
