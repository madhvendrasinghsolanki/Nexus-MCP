/**
 * Promptfoo custom provider: runs the real Nexus MCP server (demo mode) and the real agent loop
 * from clients/ts against a chosen model, and returns the answer plus a tool-call trace.
 *
 * config:
 *   provider:    "anthropic" | "openai" (any OpenAI-compatible endpoint) | "scripted"
 *   model:       model name (for "scripted": careful | gullible | lazy)
 *   baseUrl / apiKeyEnv: for openai-compatible endpoints (Ollama, Gemini, OpenRouter, Groq)
 *   exposeWrites: show write/delete tools to the model (default true: the harder safety test).
 *                 They are always denied by the client's confirmation hook and the server is a
 *                 demo with no writable backend, so nothing can be modified either way.
 */
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";
import { scriptedModel } from "./scripted-models.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ts = (f) => import(pathToFileURL(path.join(root, "clients/ts/dist", f)).href);

export default class NexusAgentProvider {
  constructor(options = {}) {
    this.config = options.config ?? {};
    this.providerId = options.id ?? `nexus-agent:${this.config.provider}:${this.config.model}`;
    this.conn = null;
  }

  id() {
    return this.providerId;
  }

  async connection() {
    if (this.conn) return this.conn;
    const { NexusConnection } = await ts("index.js");
    const python = process.env.NEXUS_PYTHON ?? path.join(root, ".venv/bin/python");
    this.conn = await NexusConnection.connectStdio({
      command: python,
      args: ["-m", "nexus_mcp"],
      cwd: root,
      env: {
        NEXUS_DEMO_MODE: "true",
        NEXUS_LOG_LEVEL: "WARNING",
        // Make write/delete tools exist so the model is actually tempted by them. The "supabase"
        // below points at a closed loopback port: a write could never reach a real database.
        NEXUS_ALLOW_WRITES: "true",
        NEXUS_ALLOW_DELETES: "true",
        PYTHONPATH: path.join(root, "src"),
        SUPABASE_URL: "http://127.0.0.1:9",
        SUPABASE_KEY: "eval-dummy-key-not-a-secret",
        SUPABASE_WRITABLE_TABLES: "notes",
        NOTION_TOKEN: "",
      },
    });
    return this.conn;
  }

  buildModel() {
    const { provider, model, baseUrl, apiKeyEnv } = this.config;
    if (provider === "scripted") return Promise.resolve(scriptedModel(model));
    return ts("index.js").then(({ createModel }) => createModel({ provider, model, baseUrl, apiKeyEnv }));
  }

  async callApi(prompt) {
    const { runAgent } = await ts("index.js");
    const tools = await this.connection();
    const specs = await tools.listTools();
    const readOnly = new Set(specs.filter((t) => t.readOnly).map((t) => t.name));
    const denied = [];
    try {
      const run = await runAgent({
        tools,
        model: await this.buildModel(),
        question: prompt,
        maxSteps: this.config.maxSteps ?? 8,
        policy: {
          allowWrites: this.config.exposeWrites ?? true,
          confirm: async (call) => {
            denied.push(call.name);
            return false; // never approve a mutation inside an eval
          },
        },
      });
      const calls = run.toolCalls.map((c) => ({ name: c.name, arguments: c.arguments, isError: c.isError }));
      return {
        output: run.answer,
        metadata: {
          toolNames: calls.map((c) => c.name),
          calls,
          attemptedMutations: calls.filter((c) => !readOnly.has(c.name)).map((c) => c.name),
          mutationsDenied: denied,
          steps: run.steps,
          stopped: run.stopped,
          advertisedTools: specs.length,
        },
      };
    } catch (err) {
      return { error: `${err instanceof Error ? err.message : String(err)}` };
    }
  }
}
