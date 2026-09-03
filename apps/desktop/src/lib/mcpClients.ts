/**
 * Installation recipes for the MCP clients people actually use. Two transports:
 *  - "network": Huddle's streamable-HTTP MCP endpoint, protected with an API key
 *    (`Authorization: Bearer hud_…`). For clients on other machines, or when the
 *    user prefers a URL over a spawned process.
 *  - "local": the stdio server started by `huddle-engine mcp --data-dir …`, for
 *    clients on this Mac. No key needed — the private data never leaves the process.
 */
export type McpClientId = "claude-code" | "claude-desktop" | "codex" | "cursor" | "copilot" | "windsurf" | "other";

export interface McpClient {
  id: McpClientId;
  label: string;
}

export const MCP_CLIENTS: McpClient[] = [
  { id: "claude-code", label: "Claude Code" },
  { id: "claude-desktop", label: "Claude Desktop" },
  { id: "codex", label: "Codex" },
  { id: "cursor", label: "Cursor" },
  { id: "copilot", label: "GitHub Copilot (VS Code)" },
  { id: "windsurf", label: "Windsurf" },
  { id: "other", label: "Other MCP client" },
];

export interface Recipe {
  /** One sentence: where the snippet goes. */
  where: string;
  /** Terminal command or file contents. */
  snippet: string;
  /** How the snippet should be labelled and highlighted. */
  kind: "command" | "json" | "toml";
  /** Optional extra remark (e.g. a restart hint). */
  note?: string;
}

const KEY_PLACEHOLDER = "hud_…";

export function networkRecipe(client: McpClientId, url: string, key: string | null): Recipe {
  const k = key ?? KEY_PLACEHOLDER;
  const bearer = `Bearer ${k}`;
  switch (client) {
    case "claude-code":
      return {
        where: "Run once in a terminal on the machine where Claude Code runs.",
        kind: "command",
        snippet: `claude mcp add --transport http huddle ${url} --header "Authorization: ${bearer}"`,
        note: "Use --scope user to make it available in every project.",
      };
    case "claude-desktop":
      return {
        where: "Claude Desktop → Settings → Developer → Edit Config (claude_desktop_config.json). Claude Desktop only starts local processes, so it reaches Huddle through the mcp-remote bridge.",
        kind: "json",
        snippet: JSON.stringify({ mcpServers: { huddle: { command: "npx", args: ["-y", "mcp-remote", url, "--header", `Authorization:${bearer}`] } } }, null, 2),
        note: "Restart Claude Desktop afterwards.",
      };
    case "codex":
      return {
        where: "Add to ~/.codex/config.toml.",
        kind: "toml",
        snippet: `[mcp_servers.huddle]\nurl = "${url}"\nhttp_headers = { Authorization = "${bearer}" }`,
      };
    case "cursor":
      return {
        where: "Cursor → Settings → MCP → Add new global MCP server (~/.cursor/mcp.json).",
        kind: "json",
        snippet: JSON.stringify({ mcpServers: { huddle: { url, headers: { Authorization: bearer } } } }, null, 2),
      };
    case "copilot":
      return {
        where: "VS Code: command “MCP: Open User Configuration” (mcp.json), or .vscode/mcp.json in a workspace.",
        kind: "json",
        snippet: JSON.stringify({ servers: { huddle: { type: "http", url, headers: { Authorization: bearer } } } }, null, 2),
      };
    case "windsurf":
      return {
        where: "Windsurf → Settings → Cascade → MCP servers → Manage (~/.codeium/windsurf/mcp_config.json).",
        kind: "json",
        snippet: JSON.stringify({ mcpServers: { huddle: { serverUrl: url, headers: { Authorization: bearer } } } }, null, 2),
      };
    default:
      return {
        where: "Any client that supports streamable-HTTP MCP: give it the URL and send the key as a bearer token.",
        kind: "json",
        snippet: JSON.stringify({ url, headers: { Authorization: bearer } }, null, 2),
      };
  }
}

const q = (a: string) => (/[\s"']/.test(a) ? `"${a.replace(/"/g, '\\"')}"` : a);

/**
 * `command`/`args` come from the shell (`engine_mcp_command`): the absolute path of the
 * program that runs the engine — the development venv's python today, the bundled
 * `huddle-engine` sidecar in a packaged build. Neither is ever on $PATH.
 */
export function localRecipe(client: McpClientId, command: string, args: string[]): Recipe {
  switch (client) {
    case "claude-code":
      return {
        where: "Run once in a terminal.",
        kind: "command",
        snippet: `claude mcp add huddle -- ${q(command)} ${args.map(q).join(" ")}`,
        note: "Use --scope user to make it available in every project.",
      };
    case "claude-desktop":
      return {
        where: "Claude Desktop → Settings → Developer → Edit Config (claude_desktop_config.json).",
        kind: "json",
        snippet: JSON.stringify({ mcpServers: { huddle: { command, args } } }, null, 2),
        note: "Restart Claude Desktop afterwards.",
      };
    case "codex":
      return {
        where: "Add to ~/.codex/config.toml.",
        kind: "toml",
        snippet: `[mcp_servers.huddle]\ncommand = ${JSON.stringify(command)}\nargs = ${JSON.stringify(args)}`,
      };
    case "cursor":
      return {
        where: "Cursor → Settings → MCP → Add new global MCP server (~/.cursor/mcp.json).",
        kind: "json",
        snippet: JSON.stringify({ mcpServers: { huddle: { command, args } } }, null, 2),
      };
    case "copilot":
      return {
        where: "VS Code: command “MCP: Open User Configuration” (mcp.json), or .vscode/mcp.json in a workspace.",
        kind: "json",
        snippet: JSON.stringify({ servers: { huddle: { type: "stdio", command, args } } }, null, 2),
      };
    case "windsurf":
      return {
        where: "Windsurf → Settings → Cascade → MCP servers → Manage (~/.codeium/windsurf/mcp_config.json).",
        kind: "json",
        snippet: JSON.stringify({ mcpServers: { huddle: { command, args } } }, null, 2),
      };
    default:
      return {
        where: "Any client that can start a stdio MCP server.",
        kind: "json",
        snippet: JSON.stringify({ command, args }, null, 2),
      };
  }
}
