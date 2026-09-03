import { useCallback, useEffect, useState } from "react";
import { Calendar, Check, Clock, Copy, Key, RefreshCw, Shield, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { native, type McpCommand, type ProxyStatus } from "@/lib/native";
import { syncNetworkProxy } from "@/lib/mcpProxy";
import type { ApiKey, McpStatus, UserSettings } from "@/types/engine";
import { fmtDate } from "@/lib/format";
import { cn } from "@/lib/utils";
import { MCP_CLIENTS, localRecipe, networkRecipe, type McpClientId, type Recipe } from "@/lib/mcpClients";
import { Badge, Button, Card, DangerDialog, Dialog, Input, Row, Select, Switch } from "@/components/ui";

type Update = (p: Partial<UserSettings>) => Promise<void>;

const CLIENT_STORAGE = "huddle.mcpClient";
const readClient = (): McpClientId => {
  try { return (localStorage.getItem(CLIENT_STORAGE) as McpClientId) || "claude-code"; } catch { return "claude-code"; }
};

function ClientSelect({ value, onChange, className }: { value: McpClientId; onChange: (c: McpClientId) => void; className?: string }) {
  return (
    <Select className={className} value={value} onChange={(e) => onChange(e.target.value as McpClientId)}>
      {MCP_CLIENTS.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
    </Select>
  );
}

function useCopy() {
  const [copied, setCopied] = useState(false);
  const copy = async (text: string) => { try { await navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch { /* ignore */ } };
  return { copied, copy };
}

/** Where-to-put-it sentence, the snippet with a copy button, and an optional note. */
function RecipeView({ recipe }: { recipe: Recipe }) {
  const { copied, copy } = useCopy();
  return (
    <div>
      <p className="text-[12.5px] text-muted">{recipe.where}</p>
      <div className="relative mt-2">
        <pre className="selectable overflow-x-auto rounded-lg border border-border bg-fg/[0.03] p-3 pr-10 font-mono text-[11.5px] leading-relaxed text-fg/85 whitespace-pre-wrap break-all">{recipe.snippet}</pre>
        <Button size="sm" variant="ghost" className="absolute right-1.5 top-1.5" title="Copy" onClick={() => copy(recipe.snippet)}>{copied ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <Copy className="h-3.5 w-3.5" />}</Button>
      </div>
      {recipe.note && <p className="mt-1.5 text-[12px] text-muted">{recipe.note}</p>}
    </div>
  );
}

export function McpSection({ settings, update }: { settings: UserSettings; update: Update }) {
  const [status, setStatus] = useState<McpStatus | null>(null);
  const [keys, setKeys] = useState<ApiKey[]>([]);
  const [newKey, setNewKey] = useState<ApiKey | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<ApiKey | null>(null);
  const [client, setClient] = useState<McpClientId>(readClient);
  const [name, setName] = useState("");
  const [days, setDays] = useState(30);
  const [cmd, setCmd] = useState<McpCommand | null>(null);
  const [proxy, setProxy] = useState<ProxyStatus | null>(null);
  const load = useCallback(() => { api.mcpStatus().then(setStatus).catch(() => {}); api.apiKeys().then(setKeys).catch(() => {}); }, []);
  useEffect(() => { load(); native.getMcpCommand().then(setCmd).catch(() => {}); }, [load, settings]);
  // The shell owns the LAN port; re-sync it whenever the MCP settings change (this also triggers
  // macOS's "allow incoming connections?" prompt for Huddle the moment network access is enabled).
  const netEnabled = settings["mcp.networkEnabled"];
  const netPort = settings["mcp.port"];
  const loopbackPort = status?.loopbackPort;
  useEffect(() => { syncNetworkProxy().then(setProxy); }, [netEnabled, netPort, loopbackPort]);
  useEffect(() => { try { localStorage.setItem(CLIENT_STORAGE, client); } catch { /* ignore */ } }, [client]);

  const clientLabel = MCP_CLIENTS.find((c) => c.id === client)?.label ?? "MCP client";
  const url = status?.addresses[0] ? `http://${status.addresses[0]}:${status.port}/mcp` : `http://<this-mac>:${settings["mcp.port"]}/mcp`;
  const openCreate = () => { setName(clientLabel); setDays(30); setCreating(true); };
  const create = async () => { const k = await api.createApiKey(name.trim() || clientLabel, days); setCreating(false); setNewKey(k); load(); };

  return (
    <>
      <Card>
        <Row label="Local MCP server" info="Lets AI apps on this Mac (Claude Code, Claude Desktop, Cursor, …) search and read your meetings.">
          <Switch checked={settings["mcp.enabled"]} onChange={(v) => update({ "mcp.enabled": v })} />
        </Row>
        <Row label="Network access" info="Also serve MCP to clients on other computers on your local network. Every client authenticates with its own API key, generated below.">
          <Switch checked={settings["mcp.networkEnabled"]} onChange={(v) => update({ "mcp.networkEnabled": v })} />
        </Row>
        {settings["mcp.networkEnabled"] && (
          <>
            <Row label="Port">
              <Input type="number" className="w-[120px]" defaultValue={settings["mcp.port"]} onBlur={(e) => { const p = Number(e.target.value); if (p > 1024 && p < 65535 && p !== settings["mcp.port"]) update({ "mcp.port": p }); }} />
            </Row>
            <Row label="Status" hint={proxy?.error ?? status?.error ?? (proxy?.running ? url : "Starting…")}>
              {proxy?.error && <Button size="sm" variant="ghost" onClick={() => native.openFirewallSettings()}><Shield className="h-3.5 w-3.5" /> Firewall settings</Button>}
              <Badge tone={proxy?.running ? "good" : proxy?.error || status?.error ? "bad" : "warn"}>{proxy?.running ? "Running on the network" : proxy?.error || status?.error ? "Not running" : "Starting"}</Badge>
            </Row>
            <Row label="Firewall" hint="macOS asks once whether Huddle may accept incoming connections. If you dismissed it, allow Huddle under System Settings → Network → Firewall → Options.">
              <Button size="sm" variant="ghost" onClick={() => native.openFirewallSettings()}><Shield className="h-3.5 w-3.5" /> Open</Button>
            </Row>
          </>
        )}
      </Card>

      {settings["mcp.networkEnabled"] && (
        <>
          <h3 className="mb-2 mt-6 flex items-center gap-2 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted"><Key className="h-3.5 w-3.5" /> API keys for MCP clients</h3>
          <Card>
            <div className="border-b border-border px-4 py-3 text-[12.5px] text-muted">
              Each MCP client that connects over the network (Claude Code, Codex, Cursor, …) sends one of these keys as a bearer token. Generate one key per client so you can revoke them individually.
            </div>
            {keys.map((k) => (
              <Row key={k.id} label={k.name} hintNode={<span className="flex flex-wrap items-center gap-3"><span className="inline-flex items-center gap-1 font-mono"><Key className="h-3 w-3" />{k.prefix}…</span><span className="inline-flex items-center gap-1"><Calendar className="h-3 w-3" />created {fmtDate(k.createdAt)}</span><span className={cn("inline-flex items-center gap-1", k.expired && "text-danger")}><Clock className="h-3 w-3" />{k.expired ? `expired ${fmtDate(k.expiresAt)}` : `expires ${fmtDate(k.expiresAt)}`}</span></span>}>
                {k.expired && <Badge tone="bad">Expired</Badge>}
                <Button size="sm" onClick={async () => { await api.renewApiKey(k.id); load(); }}><RefreshCw className="h-3 w-3" /> Renew</Button>
                <Button size="sm" variant="ghost" onClick={() => setDeleting(k)}><Trash2 className="h-3.5 w-3.5" /></Button>
              </Row>
            ))}
            {keys.length === 0 && <div className="px-4 py-3 text-[12.5px] text-muted">No keys yet.</div>}
            <div className="flex items-center justify-start border-t border-border px-4 py-3">
              <Button variant="primary" onClick={openCreate}><Key className="h-3.5 w-3.5" /> Generate new key</Button>
            </div>
          </Card>

          <DangerDialog open={!!deleting} onClose={() => setDeleting(null)} title="Delete this API key?" confirmLabel="Delete key" seconds={3}
            onConfirm={async () => { if (deleting) { await api.deleteApiKey(deleting.id); setDeleting(null); load(); } }}>
            The MCP client using “{deleting?.name}” ({deleting?.prefix}…) loses access immediately. This cannot be undone.
          </DangerDialog>

          <Dialog open={creating} onClose={() => setCreating(false)} title="New API key for an MCP client" width={460}
            footer={<><Button variant="ghost" onClick={() => setCreating(false)}>Cancel</Button><Button variant="primary" onClick={create}><Key className="h-3.5 w-3.5" /> Generate key</Button></>}>
            <label className="block text-[12px] text-muted">MCP client</label>
            <ClientSelect className="mt-1 !w-full" value={client} onChange={(c) => { setClient(c); setName(MCP_CLIENTS.find((x) => x.id === c)?.label ?? ""); }} />
            <p className="mt-1 text-[11.5px] text-muted">After generating, you get the exact setup steps for this client with the key filled in.</p>
            <label className="mt-3 block text-[12px] text-muted">Key name</label>
            <Input autoFocus placeholder="e.g. Cursor on the studio Mac" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && create()} className="mt-1" />
            <label className="mt-3 block text-[12px] text-muted">Valid for</label>
            <div className="mt-1 flex gap-2">
              {[30, 60, 90].map((d) => (
                <button key={d} onClick={() => setDays(d)} className={cn("flex-1 rounded-lg border px-3 py-2 text-[13px]", days === d ? "border-accent bg-accent-soft font-medium text-accent" : "border-border hover:bg-fg/[0.04]")}>{d} days</button>
              ))}
            </div>
            <p className="mt-3 text-[11.5px] text-muted">Expired keys stop working immediately. Renewing extends a key by the same period; creating a new key and deleting the old one is the safer routine.</p>
          </Dialog>

          <Dialog open={!!newKey} onClose={() => setNewKey(null)} title={`Connect ${clientLabel}`} width={560} footer={<Button variant="primary" onClick={() => setNewKey(null)}>Done</Button>}>
            <div className="mb-3 flex items-center gap-2 rounded-lg border border-accent/30 bg-accent-soft/60 px-3 py-2 text-[12.5px]">
              <Key className="h-3.5 w-3.5 shrink-0 text-accent" />
              <span>The key is included in the snippet below and shown only once. Copy it now.</span>
            </div>
            <div className="mb-2 flex items-center justify-between gap-3">
              <span className="text-[12px] text-muted">Setup for</span>
              <ClientSelect value={client} onChange={setClient} />
            </div>
            <RecipeView recipe={networkRecipe(client, url, newKey?.key ?? null)} />
          </Dialog>
        </>
      )}

      <h3 className="mb-2 mt-6 font-display text-[11.5px] font-bold uppercase tracking-wider text-muted">Connect a client on this Mac</h3>
      <Card>
        <div className="px-4 py-3">
          <div className="mb-3 flex items-center justify-between gap-3">
            <span className="text-[12.5px] text-muted">No API key needed: the client starts Huddle’s MCP server itself.</span>
            <ClientSelect value={client} onChange={setClient} />
          </div>
          <RecipeView recipe={localRecipe(client, cmd?.program ?? "huddle-engine", cmd?.args ?? ["mcp"])} />
          {cmd?.development && <p className="mt-2 text-[11.5px] text-muted">Development build: the command points at the engine’s Python environment in the repository. A packaged Huddle uses its bundled engine instead.</p>}
        </div>
      </Card>
    </>
  );
}
