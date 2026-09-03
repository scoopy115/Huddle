import { api } from "@/lib/api";
import { native, type ProxyStatus } from "@/lib/native";

/**
 * Keep the shell's LAN forwarder in step with the MCP settings: when "Network access" is on and
 * the engine's loopback MCP server is up, forward `mcp.port` to it; otherwise stop. Called on
 * engine start (App) and after every MCP setting change (Settings → MCP). Errors are returned,
 * never thrown, so a firewall refusal or a busy port shows up as text next to the switch.
 */
export async function syncNetworkProxy(): Promise<ProxyStatus | null> {
  try {
    const [settings, status] = await Promise.all([api.getSettings(), api.mcpStatus()]);
    if (!settings["mcp.networkEnabled"] || !status.loopbackPort) return await native.networkProxyStop();
    return await native.networkProxyStart(Number(settings["mcp.port"]), status.loopbackPort);
  } catch (e) {
    return { running: false, port: null, targetPort: null, error: e instanceof Error ? e.message : String(e) };
  }
}
