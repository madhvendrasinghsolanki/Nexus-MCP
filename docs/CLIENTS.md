# Connecting clients

Nexus MCP speaks standard MCP, so any MCP client can use it. Two transports are supported:

| Transport | Use it for | Start it with |
|---|---|---|
| **stdio** (default) | Local apps that launch the server themselves (Claude Desktop, Claude Code, Cursor, VS Code, Gemini CLI) | `python -m nexus_mcp` |
| **streamable-http** | URL-based clients | `nexus-mcp --transport streamable-http` serves `http://127.0.0.1:8000/mcp` |

> **HTTP security:** set `NEXUS_HTTP_AUTH_TOKEN` (32+ chars) and send `Authorization: Bearer <token>`. The server refuses to listen on a non-loopback host without a token. It does not terminate TLS; put a reverse proxy in front for remote use. See `docs/SECURITY.md`.

## 0. Try it with no accounts (demo mode)

`NEXUS_DEMO_MODE=true` loads two fictional sources (`demo_wiki`, `demo_tickets`) so you can test any client without Notion or Supabase. Try: *"What was the root cause of the September API latency incident, and which ticket tracked the fix?"*

## 1. Install once

```bash
git clone <your-fork> nexus-mcp && cd nexus-mcp
uv venv && uv pip install -e ".[dev]"
```

Use **absolute paths** in every config below. GUI apps do not inherit your shell's `PATH`, so a bare `python` often fails. Replace `/ABS/PATH/nexus-mcp` accordingly (on Windows use `.venv\Scripts\python.exe`).

## 2. Claude (and other MCP apps)

### Claude Desktop
Settings, Developer, **Edit Config** opens `claude_desktop_config.json` (macOS: `~/Library/Application Support/Claude/`, Windows: `%APPDATA%\Claude\`). Restart the app after saving.

```json
{
  "mcpServers": {
    "nexus": {
      "command": "/ABS/PATH/nexus-mcp/.venv/bin/python",
      "args": ["-m", "nexus_mcp"],
      "env": {
        "NEXUS_DEMO_MODE": "true"
      }
    }
  }
}
```

For real data, replace the demo flag with `NOTION_TOKEN`, `SUPABASE_URL`, `SUPABASE_KEY` and the policy variables from `.env.example`. Server logs appear in `~/Library/Logs/Claude/mcp-server-nexus.log` (macOS) or `%APPDATA%\Claude\logs` (Windows).

### Claude Code
Claude's options go **before** the server name; everything after `--` is the server command.

```bash
claude mcp add --transport stdio --env NEXUS_DEMO_MODE=true nexus \
  -- /ABS/PATH/nexus-mcp/.venv/bin/python -m nexus_mcp
claude mcp list        # verify
```

To share a setup with a team without committing secrets, use a project-scoped `.mcp.json` with `${VAR}` expansion:

```json
{
  "mcpServers": {
    "nexus": {
      "type": "stdio",
      "command": "/ABS/PATH/nexus-mcp/.venv/bin/python",
      "args": ["-m", "nexus_mcp"],
      "env": {
        "NOTION_TOKEN": "${NOTION_TOKEN}",
        "SUPABASE_URL": "${SUPABASE_URL}",
        "SUPABASE_KEY": "${SUPABASE_KEY}"
      }
    }
  }
}
```

HTTP (after starting the server yourself):

```bash
claude mcp add --transport http nexus http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer $NEXUS_HTTP_AUTH_TOKEN"
```

### Cursor
`.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global); same `mcpServers` shape as Claude Desktop.

### VS Code
`.vscode/mcp.json` uses a `servers` key (not `mcpServers`):

```json
{
  "servers": {
    "nexus": {
      "type": "stdio",
      "command": "/ABS/PATH/nexus-mcp/.venv/bin/python",
      "args": ["-m", "nexus_mcp"]
    }
  }
}
```

Pass secrets through VS Code input variables or an environment file rather than hard-coding them. The `env`/`inputs` field names are not on the page I checked, so confirm them in VS Code's MCP configuration reference.

### Gemini CLI (free tier available)
`~/.gemini/settings.json` (or `.gemini/settings.json` per project). Gemini CLI **does not pass your shell environment through**; list every variable the server needs under `env`. Avoid underscores in the server name.

```json
{
  "mcpServers": {
    "nexus": {
      "command": "/ABS/PATH/nexus-mcp/.venv/bin/python",
      "args": ["-m", "nexus_mcp"],
      "env": { "NEXUS_DEMO_MODE": "true", "NOTION_TOKEN": "$NOTION_TOKEN" },
      "timeout": 15000,
      "trust": false
    }
  }
}
```

Keep `trust` false so write tools still prompt for confirmation.

## 3. Any other model: `nexus-client` (TypeScript)

`clients/ts` is a small agent that connects to the server and drives **any** tool-calling model, so you can compare models on the same tools. It supports the Anthropic Messages API and any **OpenAI-compatible** endpoint (Ollama locally, Gemini, OpenRouter, Groq, vLLM, LM Studio).

```bash
cd clients/ts && npm install && npm run build

# Claude
ANTHROPIC_API_KEY=... node dist/cli.js "Why did we choose PostgreSQL?" --demo \
  -- /ABS/PATH/nexus-mcp/.venv/bin/python -m nexus_mcp

# Local, free: Ollama (pull a model that supports tool calling first)
node dist/cli.js "Which tickets are high priority?" --demo --provider openai --model llama3.1 \
  -- /ABS/PATH/nexus-mcp/.venv/bin/python -m nexus_mcp

# Gemini via its OpenAI-compatible endpoint (verify URL and model name in Google's docs)
GEMINI_API_KEY=... node dist/cli.js "..." --demo --provider openai \
  --base-url https://generativelanguage.googleapis.com/v1beta/openai/ \
  --api-key-env GEMINI_API_KEY --model <model> \
  -- /ABS/PATH/nexus-mcp/.venv/bin/python -m nexus_mcp
```

Free tiers, model names and tool-calling quality change often and vary a lot between small local models; check each provider's current docs. The Promptfoo suite (`docs/EVALS.md`) is how this project compares models objectively.

### Client-side safety (defence in depth)
- The model only sees **read-only tools** (from MCP `readOnlyHint`); tools without annotations are treated as writes.
- `--allow-writes` exposes write tools, and **each call still needs a typed `y`**; with no confirmation hook, writes are denied.
- A tool call for a name the model was never shown is refused, not forwarded.
- Tool output is truncated and the system prompt tells the model it is untrusted data. The demo wiki contains a deliberate prompt-injection page (`wiki-005`) used to test this.

## Troubleshooting
- **Server missing / red in the client:** run the exact command by hand first (`/ABS/PATH/.venv/bin/python -m nexus_mcp --check-config`); it validates configuration and prints a secret-free summary.
- **Invalid configuration:** the server exits with field names only (never values). Check `.env.example`.
- **No write tools:** by design. Set `NEXUS_ALLOW_WRITES=true` plus the `*_WRITABLE_*` allowlists; check `server_info` to see what is enabled.
- **Notion returns not found:** share the page with your integration (page menu, Connections).
