import { useCallback, useEffect, useState } from "react";
import { Check, Server, ShieldCheck, Trash2, Unplug } from "lucide-react";
import { api, errorMessage } from "@/lib/api";
import type { ServerStatus, ServerTestResult, UserSettings } from "@/types/engine";
import { Badge, Button, Card, DangerDialog, Dialog, Input, Row, Select } from "@/components/ui";

type Update = (p: Partial<UserSettings>) => Promise<void>;

/** Settings → Server: connect this Mac to a self-hosted Huddle Server. */
export function ServerSection({ settings, update }: { settings: UserSettings; update: Update }) {
  const [status, setStatus] = useState<ServerStatus | null>(null);
  const [url, setUrl] = useState(settings["server.url"] ?? "");
  const [key, setKey] = useState(settings["server.apiKey"] ?? "");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ServerTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [forgetting, setForgetting] = useState(false);
  const load = useCallback(() => { api.serverStatus().then(setStatus).catch(() => {}); }, []);
  useEffect(() => { load(); }, [load, settings]);

  const connect = async () => {
    setBusy(true); setError(null); setResult(null);
    try {
      await update({ "server.url": url.trim(), "server.apiKey": key.trim() });
      setResult(await api.serverTest());
      load();
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  };
  const trust = async () => {
    if (!result?.untrustedCertificate) return;
    setBusy(true); setError(null);
    try { await api.serverTrust(result.untrustedCertificate.fingerprint); setResult(await api.serverTest()); load(); }
    catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  };
  const forget = async () => { await api.serverForget(); setUrl(""); setKey(""); setResult(null); setForgetting(false); await update({}); load(); };

  const configured = !!status?.configured;
  const caps = result?.server?.capabilities;
  return (
    <>
      <Card>
        <Row label="Server address" hint="The address of your Huddle Server, for example https://huddle.example.com or https://192.168.1.20.">
          <Input className="w-[300px]" placeholder="https://" value={url} onChange={(e) => setUrl(e.target.value)} spellCheck={false} />
        </Row>
        <Row label="Client key" hint="Generated in the server dashboard under Clients. One key per Mac.">
          <Input className="w-[300px] font-mono" placeholder="hsk_…" type="password" value={key} onChange={(e) => setKey(e.target.value)} spellCheck={false} />
        </Row>
        <div className="flex items-center gap-2 border-t border-border px-4 py-3">
          <Button variant="primary" loading={busy} disabled={!url.trim() || !key.trim()} onClick={connect}><Server className="h-3.5 w-3.5" /> {configured ? "Save and test" : "Connect"}</Button>
          {configured && <Button variant="ghost" onClick={() => setForgetting(true)}><Unplug className="h-3.5 w-3.5" /> Disconnect</Button>}
          <div className="flex-1" />
          {status?.configured && (
            <Badge tone={result ? (result.ok ? "good" : "bad") : "neutral"}>
              {result ? (result.ok ? `Connected to ${result.server?.name ?? status.name}` : "Not reachable") : status.name || status.url}
            </Badge>
          )}
          {status?.trusted && <Badge tone="neutral" title={`Pinned certificate ${settings["server.caFingerprint"]}`}><ShieldCheck className="h-3 w-3" /> Certificate pinned</Badge>}
        </div>
      </Card>
      {(error || (result && !result.ok && !result.untrustedCertificate)) && <div className="mt-2 text-[12.5px] text-danger">{error ?? result?.error}</div>}

      {result?.ok && caps && (
        <Card className="mt-3">
          <Row label="Transcription" hint={caps.transcription ?? "No model installed on the server yet"}><Badge tone={caps.transcription ? "good" : "warn"}>{caps.transcription ? "Ready" : "Missing"}</Badge></Row>
          <Row label="Speaker detection"><Badge tone={caps.diarization ? "good" : "warn"}>{caps.diarization ? "Ready" : "Missing"}</Badge></Row>
          <Row label="Notes" hint={caps.llm ? `AI model ${caps.llm}` : "No AI model on the server — notes are written on this Mac"}><Badge tone={caps.llm ? "good" : "neutral"}>{caps.llm ? "On the server" : "On this Mac"}</Badge></Row>
          <Row label="Server version" hint={`Huddle Server ${result.server?.version} · engine ${result.server?.engineVersion}`}><Check className="h-3.5 w-3.5 text-emerald-600" /></Row>
        </Card>
      )}

      {configured && (
        <>
          <h3 className="mb-2 mt-6 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">New recordings</h3>
          <Card>
            <Row label="Process on" hint="Ask: choose after every recording. The server keeps the audio and the notes; this Mac imports the result.">
              <Select value={settings["server.defaultTarget"] ?? "ask"} onChange={(e) => update({ "server.defaultTarget": e.target.value as UserSettings["server.defaultTarget"] })}>
                <option value="ask">Ask every time</option>
                <option value="local">This Mac</option>
                <option value="remote">The server</option>
              </Select>
            </Row>
          </Card>
        </>
      )}

      <Dialog open={!!result?.untrustedCertificate} onClose={() => setResult(null)} title="Trust this server?" width={520}
        footer={<><Button variant="ghost" onClick={() => setResult(null)}>Cancel</Button><Button variant="primary" loading={busy} onClick={trust}><ShieldCheck className="h-3.5 w-3.5" /> Trust</Button></>}>
        <p className="mb-3 text-muted">The server uses its own certificate. Compare the fingerprint with the one shown in the server dashboard under Overview. Trust it only when they match.</p>
        <div className="selectable rounded-lg border border-border bg-fg/[0.03] p-3 font-mono text-[11.5px] leading-relaxed break-all">{result?.untrustedCertificate?.fingerprint}</div>
        <p className="mt-2 text-[11.5px] text-muted">{result?.untrustedCertificate?.subject}</p>
      </Dialog>

      <DangerDialog open={forgetting} onClose={() => setForgetting(false)} title="Disconnect from the server?" confirmLabel="Disconnect" seconds={0} onConfirm={forget}>
        The address, the client key and the pinned certificate are removed from this Mac. Meetings already processed stay as they are.
        <span className="mt-2 block"><Trash2 className="mr-1 inline h-3 w-3" />Nothing is deleted on the server.</span>
      </DangerDialog>
    </>
  );
}
