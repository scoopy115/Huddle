import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { AlertTriangle } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import { native, type EngineStatus, type RecordingMeta } from "@/lib/native";
import { resetAudio, setSoundsEnabled, sounds } from "@/lib/sounds";
import { syncNetworkProxy } from "@/lib/mcpProxy";
import { syncShellPrefs } from "@/lib/shellPrefs";
import { checkForUpdates, scheduleUpdateChecks } from "@/lib/updates";
import { UpdateDialog } from "@/components/UpdateDialog";
import { PermissionsReminder } from "@/components/PermissionsReminder";
import { NavContext, type View } from "@/lib/nav";
import type { Meeting, UserSettings } from "@/types/engine";
import { Sidebar } from "@/components/Sidebar";
import { CommandPalette } from "@/components/CommandPalette";
import { configureLocale } from "@/lib/format";
import { BrandMark, Button, Dialog, Spinner } from "@/components/ui";
import { ActionItemsScreen } from "@/screens/ActionItemsScreen";
import { AskScreen } from "@/screens/AskScreen";
import { ProcessesScreen } from "@/screens/ProcessesScreen";
import { MeetingScreen } from "@/screens/MeetingScreen";
import { MeetingsScreen } from "@/screens/MeetingsScreen";
import { OnboardingScreen } from "@/screens/OnboardingScreen";
import { RecordScreen } from "@/screens/RecordScreen";
import { SearchScreen } from "@/screens/SearchScreen";
import { SettingsScreen } from "@/screens/SettingsScreen";

export default function App() {
  const [view, setView] = useState<View>({ kind: "meetings" });
  const [engine, setEngine] = useState<EngineStatus>({ state: "starting", port: null, message: null, command: null, logPath: null });
  const [meetings, setMeetings] = useState<Meeting[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [recording, setRecording] = useState(false);
  const [settings, setSettings] = useState<UserSettings | null>(null);
  const [onboarded, setOnboarded] = useState<boolean | null>(null);
  const [unfinished, setUnfinished] = useState<RecordingMeta[]>([]);
  const [toast, setToast] = useState<string | null>(null);
  // AI availability: the llm resolution from the setup plan. Polled while missing so a download
  // finishing in Settings lights the features up without a restart.
  const [ai, setAi] = useState<{ ready: boolean; reason: string | null }>({ ready: true, reason: null });
  // The setup screen returns on every launch while a Whisper or AI model is missing; "Skip for
  // now" only hides it for the rest of this session.
  const [needsSetup, setNeedsSetup] = useState(false);
  const [setupDismissed, setSetupDismissed] = useState(false);
  const refreshAi = useCallback(async () => {
    try {
      const plan = await api.setupPlan();
      const llm = plan.resolutions.find((r) => r.task === "llm");
      setAi({ ready: llm?.status === "ready", reason: llm?.reason ?? null });
      const ok = (s: string | undefined) => s === "ready" || s === "builtin";
      setNeedsSetup(plan.resolutions.some((r) => (r.task === "transcription" || r.task === "llm") && !ok(r.status)));
    } catch { /* engine not reachable yet */ }
  }, []);
  useEffect(() => {
    if (engine.state !== "ready") return;
    refreshAi();
    if (ai.ready && !needsSetup) return;
    const t = setInterval(refreshAi, 15000);
    return () => clearInterval(t);
  }, [engine.state, ai.ready, needsSetup, refreshAi]);
  const [palette, setPalette] = useState(false);

  const go = useCallback((v: View) => setView(v.kind === "meeting" ? { ...v, nonce: Date.now() } : v), []);

  const refreshMeetings = useCallback(async () => {
    if (engine.state !== "ready") return;
    setLoading(true);
    try { setMeetings(await api.listMeetings()); } catch (e) { setToast(errorMessage(e)); } finally { setLoading(false); }
  }, [engine.state]);

  const importAudio = useCallback(async () => {
    const picked = await open({ multiple: false, filters: [{ name: "Audio", extensions: ["wav", "mp3", "m4a", "mp4", "webm", "flac", "ogg", "aac", "mov"] }] });
    if (!picked) return;
    const path = typeof picked === "string" ? picked : (picked as { path: string }).path;
    try {
      const m = await api.importFile(path);
      await refreshMeetings();
      go({ kind: "meeting", id: m.id });
    } catch (e) { setToast(errorMessage(e)); }
  }, [refreshMeetings, go]);

  // Shortcuts are accelerators of the native menu (Rust builds it; macOS delivers them as
  // `menu` events before the webview sees the key).
  const menuAction = useCallback((id: string) => {
    switch (id) {
      case "settings": go({ kind: "settings" }); break;
      case "new-recording": go({ kind: "record" }); break;
      case "import-audio": importAudio(); break;
      case "view-meetings": go({ kind: "meetings" }); break;
      case "view-ask": go({ kind: "ask" }); break;
      case "view-actions": go({ kind: "actions" }); break;
      case "view-processes": go({ kind: "processes" }); break;
      case "view-search": setPalette((p) => !p); sounds.open(); break;
      case "check-updates":
        checkForUpdates({ manual: true }).then((s) => {
          if (s.error) setToast(`Could not check for updates: ${s.error}`);
          else if (!s.available) setToast(`Huddle ${s.currentVersion} is up to date.`);
        });
        break;
    }
  }, [go, importAudio]);
  useEffect(() => {
    let un: (() => void) | undefined;
    native.onMenu(menuAction).then((u) => (un = u));
    return () => un?.();
  }, [menuAction]);

  // Recordings started from the menu bar or ⌥⌘R: the shell records, the UI turns the result into
  // a meeting once the engine is up. `recording:stopped` wakes a visible window; the pending
  // queue covers the case where the window was hidden or the engine was stopped meanwhile.
  const submitPending = useCallback(async () => {
    if (engine.state !== "ready") return;
    const list = await native.takePendingRecordings().catch(() => [] as RecordingMeta[]);
    for (const r of list) {
      if (r.status !== "saved" || r.durationSec < 1) { if (r.error) setToast(r.error); continue; }
      try {
        const meeting = await api.createFromRecording({ id: r.id, filePath: r.filePath, systemFilePath: r.systemFilePath ?? null, startedAt: r.startedAt, durationSec: r.durationSec, inputDevice: r.inputDevice, sampleRate: r.sampleRate, channels: r.channels, format: r.format, source: "recorded", process: true });
        await refreshMeetings();
        go({ kind: "meeting", id: meeting.id });
      } catch (e) { setToast(errorMessage(e)); }
    }
  }, [engine.state, go, refreshMeetings]);
  useEffect(() => { submitPending(); }, [submitPending]);
  useEffect(() => {
    const uns: (() => void)[] = [];
    // The shell plays the chimes for recordings it started itself.
    native.onRecordingStarted(() => { setRecording(true); resetAudio(); }).then((u) => uns.push(u));
    native.onRecordingStopped(() => { setRecording(false); resetAudio(); submitPending(); }).then((u) => uns.push(u));
    return () => uns.forEach((u) => u());
  }, [submitPending]);
  useEffect(() => {
    let un: (() => void) | undefined;
    native.onShellPrefsChanged((p) => {
      const patch = { "recording.inputDevice": p.inputDevice, "recording.systemAudio": p.systemAudio } as Partial<UserSettings>;
      // Mirror everything the shell knows, so this copy never lags the Settings screen (a stale
      // `general.sounds` here used to switch sounds back on right after they were turned off).
      setSettings((s) => (s ? { ...s, ...patch, "general.sounds": p.sounds, "general.menuBar": p.menuBar } : s));
      api.updateSettings(patch).catch(() => {});
    }).then((u) => (un = u));
    return () => un?.();
  }, []);
  useEffect(() => { if (settings) scheduleUpdateChecks(settings["general.autoUpdate"] !== false); }, [settings]);

  // Sounds follow the setting; a meeting that finishes processing gets a small chime.
  useEffect(() => { if (settings) setSoundsEnabled(settings["general.sounds"] !== false); }, [settings]);
  const wasProcessing = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!meetings) return;
    const now = new Set(meetings.filter((m) => m.status === "processing").map((m) => m.id));
    for (const id of wasProcessing.current) {
      const m = meetings.find((x) => x.id === id);
      if (m && m.status === "ready") sounds.success();
      else if (m && m.status === "failed") sounds.error();
    }
    wasProcessing.current = now;
  }, [meetings]);

  // Engine lifecycle
  useEffect(() => {
    native.getLocalePrefs().then((p) => configureLocale(p.locale, p.force24Hour)).catch(() => {});
    native.engineStatus().then(setEngine).catch(() => {});
    let un: (() => void) | undefined;
    native.onEngineStatus(setEngine).then((u) => (un = u));
    return () => un?.();
  }, []);

  // Once the engine is up: settings, meetings, crash recovery.
  useEffect(() => {
    if (engine.state !== "ready") return;
    (async () => {
      try {
        let s = await api.getSettings();
        // Shell prefs: the shell owns the recording choice (the menu-bar popover can change it
        // while the engine sleeps), the engine owns the menu-bar toggle.
        try {
          const sp = await native.getShellPrefs();
          native.setShellPrefs({ menuBar: s["general.menuBar"] !== false, sounds: s["general.sounds"] !== false }).catch(() => {});
          const patch: Partial<UserSettings> = {};
          if ((sp.inputDevice ?? null) !== (s["recording.inputDevice"] ?? null)) patch["recording.inputDevice"] = sp.inputDevice;
          if (sp.systemAudio !== !!s["recording.systemAudio"]) patch["recording.systemAudio"] = sp.systemAudio;
          if (Object.keys(patch).length) s = await api.updateSettings(patch);
        } catch { /* shell prefs unavailable */ }
        setSettings(s);
        setOnboarded(Boolean(s["onboarding.completed"]));
      } catch (e) { setToast(errorMessage(e)); }
      refreshMeetings();
      syncNetworkProxy();
      try {
        const status = await native.recordingStatus();
        setRecording(status.recording);
        const list = await native.listUnfinishedRecordings();
        if (!status.recording && list.length) setUnfinished(list);
      } catch { /* recorder status unavailable */ }
    })();
  }, [engine.state, refreshMeetings]);

  // Keep the list fresh while anything is processing.
  const anyProcessing = useMemo(() => meetings?.some((m) => m.status === "processing") ?? false, [meetings]);
  useEffect(() => {
    if (!anyProcessing) return;
    const t = setInterval(refreshMeetings, 2500);
    return () => clearInterval(t);
  }, [anyProcessing, refreshMeetings]);

  const openActions = useMemo(() => meetings?.reduce((n, m) => n + m.openActionCount, 0) ?? 0, [meetings]);
  const running = useMemo(() => meetings?.filter((m) => m.status === "processing").length ?? 0, [meetings]);
  useEffect(() => { native.setTrayBusy(running > 0).catch(() => {}); }, [running]);

  const recover = async (keep: boolean) => {
    const list = unfinished;
    setUnfinished([]);
    if (!keep) {
      // Dismissing is a decision: the files go, so the prompt does not come back on every launch.
      native.discardUnfinishedRecordings(list.map((r) => r.id)).catch(() => {});
      return;
    }
    for (const r of list) {
      try {
        await api.createFromRecording({ id: r.id, filePath: r.filePath, systemFilePath: r.systemFilePath ?? null, startedAt: r.startedAt, durationSec: r.durationSec, inputDevice: r.inputDevice, sampleRate: r.sampleRate, channels: r.channels, format: r.format, source: "recovered", title: "Recovered recording", process: true });
      } catch (e) { setToast(errorMessage(e)); }
    }
    refreshMeetings();
  };

  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(null), 5000); return () => clearTimeout(t); }, [toast]);

  const content = () => {
    if (engine.state === "failed" || engine.state === "stopped") {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-3 p-10 text-center">
          <AlertTriangle className="h-7 w-7 text-danger" />
          <div className="text-[15px] font-medium">The local processing engine could not start</div>
          <div className="max-w-md text-[13px] text-muted">{engine.message}</div>
          {engine.logPath && <div className="selectable font-mono text-[11px] text-muted">{engine.logPath}</div>}
          <Button variant="primary" onClick={() => native.engineRestart().then(setEngine)}>Try again</Button>
        </div>
      );
    }
    if (engine.state !== "ready" || onboarded === null) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-4 text-[13px] text-muted">
          <BrandMark className="h-12 w-12 text-accent animate-record" />
          <span className="inline-flex items-center gap-2"><Spinner /> Loading…</span>
        </div>
      );
    }
    if ((!onboarded || (needsSetup && !setupDismissed)) && view.kind !== "settings") {
      return <OnboardingScreen returning={!!onboarded} onDone={() => { setOnboarded(true); setSetupDismissed(true); refreshAi(); }} />;
    }
    switch (view.kind) {
      case "meetings": return <MeetingsScreen meetings={meetings} loading={loading} onImport={importAudio} onChanged={refreshMeetings} />;
      case "meeting": return <MeetingScreen id={view.id} seek={view.seek} segmentId={view.segmentId} nonce={view.nonce} onChanged={refreshMeetings} />;
      case "record": return settings ? <RecordScreen settings={settings} onSettings={(p) => { setSettings((s) => (s ? { ...s, ...p } : s)); api.updateSettings(p).catch(() => {}); syncShellPrefs(p); }} onRecordingStateChange={setRecording} /> : null;
      case "search": return <SearchScreen initialQuery={view.query} nonce={view.nonce} />;
      case "ask": return <AskScreen meetings={meetings ?? []} />;
      case "processes": return <ProcessesScreen onChanged={refreshMeetings} />;
      case "actions": return <ActionItemsScreen onChanged={refreshMeetings} />;
      case "settings": return <SettingsScreen section={view.section} engine={engine} />;
      case "onboarding": return <OnboardingScreen returning={!!onboarded} onDone={() => { setOnboarded(true); setSetupDismissed(true); refreshAi(); go({ kind: "meetings" }); }} />;
    }
  };

  return (
    <NavContext.Provider value={{ view, go, ai: { ...ai, refresh: refreshAi } }}>
      <div className="flex h-full">
        <Sidebar engine={engine} openActions={openActions} recording={recording} running={running} />
        <main className="relative min-w-0 flex-1 bg-bg">
          {content()}
          {toast && (
            <div className="absolute bottom-4 left-1/2 z-40 -translate-x-1/2 panel px-3 py-2 text-[12.5px] shadow-lg">{toast}</div>
          )}
        </main>
      </div>
      {engine.state === "ready" && onboarded && <CommandPalette open={palette} onClose={() => setPalette(false)} meetings={meetings ?? []} />}
      <UpdateDialog />
      {onboarded && <PermissionsReminder />}
      <Dialog open={unfinished.length > 0} onClose={() => recover(false)} title="Recover unfinished recording?"
        footer={<><Button onClick={() => recover(false)}>Discard</Button><Button variant="primary" onClick={() => recover(true)}>Recover and process</Button></>}>
        Huddle closed while {unfinished.length === 1 ? "a recording was" : `${unfinished.length} recordings were`} in progress. The audio up to the last second was saved to disk and can be processed now, or discarded for good.
      </Dialog>
    </NavContext.Provider>
  );
}
