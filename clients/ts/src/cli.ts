#!/usr/bin/env node
import { parseArgs } from "node:util";
import { createInterface } from "node:readline/promises";
import { runAgent } from "./agent.js";
import { NexusConnection } from "./connection.js";
import { createModel, DEFAULT_MODELS } from "./models/index.js";

const USAGE = `nexus-client "<question>" [options]

  --provider anthropic|openai   Model API style (default: anthropic). "openai" = any
                                OpenAI-compatible endpoint: Ollama, Gemini, OpenRouter, Groq...
  --model <id>                  Model name (defaults: claude-sonnet-5-5 / llama3.1)
  --base-url <url>              Endpoint for --provider openai (default: http://localhost:11434/v1)
  --api-key-env <VAR>           Name of the env var that holds the API key
  --demo                        Use built-in fictional data (no Notion/Supabase needed)
  -- <cmd> [args...]            Command that starts the server (default: nexus-mcp), e.g.
                                -- python -m nexus_mcp
  --allow-writes                Expose write tools; each call still asks you to confirm
  --max-steps <n>               Tool-use step limit (default 8)
  --json                        Print the full run as JSON
`;

async function main(): Promise<number> {
  // Everything after `--` is the server command, exactly like `claude mcp add ... -- cmd args`.
  const argv = process.argv.slice(2);
  const sep = argv.indexOf("--");
  const ownArgs = sep === -1 ? argv : argv.slice(0, sep);
  const serverCmd = sep === -1 ? [] : argv.slice(sep + 1);
  const { values, positionals } = parseArgs({
    args: ownArgs,
    allowPositionals: true,
    options: {
      provider: { type: "string", default: "anthropic" },
      model: { type: "string" },
      "base-url": { type: "string" },
      "api-key-env": { type: "string" },
      demo: { type: "boolean", default: false },
      "allow-writes": { type: "boolean", default: false },
      "max-steps": { type: "string", default: "8" },
      json: { type: "boolean", default: false },
      help: { type: "boolean", default: false },
    },
  });
  const question = positionals.join(" ").trim();
  if (values.help || !question) {
    process.stderr.write(USAGE);
    return values.help ? 0 : 2;
  }
  if (values.provider !== "anthropic" && values.provider !== "openai") {
    process.stderr.write("--provider must be 'anthropic' or 'openai'\n");
    return 2;
  }
  const provider = values.provider;
  const model = createModel({
    provider,
    model: values.model ?? DEFAULT_MODELS[provider],
    baseUrl: values["base-url"],
    apiKeyEnv: values["api-key-env"],
  });

  const passthrough = Object.fromEntries(
    Object.entries(process.env).filter(
      ([k, v]) => v !== undefined && /^(NEXUS_|SUPABASE_|NOTION_)/.test(k),
    ),
  ) as Record<string, string>;
  const conn = await NexusConnection.connectStdio({
    command: serverCmd[0] ?? "nexus-mcp",
    args: serverCmd.slice(1),
    env: { ...passthrough, ...(values.demo ? { NEXUS_DEMO_MODE: "true" } : {}) },
  });

  const rl = createInterface({ input: process.stdin, output: process.stderr });
  try {
    const run = await runAgent({
      tools: conn,
      model,
      question,
      maxSteps: Number.parseInt(values["max-steps"] ?? "8", 10),
      policy: {
        allowWrites: values["allow-writes"] === true,
        confirm: async (call) => {
          const answer = await rl.question(`Allow ${call.name} ${JSON.stringify(call.arguments)}? [y/N] `);
          return answer.trim().toLowerCase() === "y";
        },
      },
      onEvent: (e) => {
        if (e.type === "tool_call") process.stderr.write(`→ ${e.call.name}(${JSON.stringify(e.call.arguments)})\n`);
      },
    });
    process.stdout.write(values.json ? `${JSON.stringify(run, null, 2)}\n` : `${run.answer}\n`);
    return run.stopped === "final" ? 0 : 1;
  } finally {
    rl.close();
    await conn.close();
  }
}

main().then(
  (code) => process.exit(code),
  (err: unknown) => {
    process.stderr.write(`error: ${err instanceof Error ? err.message : String(err)}\n`);
    process.exit(1);
  },
);
