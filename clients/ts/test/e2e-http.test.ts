/** Full path: built CLI -> real HTTP model endpoint (local stand-in) -> real MCP server over stdio. */
import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { runAgent } from "../src/agent.js";
import { NexusConnection } from "../src/connection.js";
import { OpenAICompatModel } from "../src/models/openai.js";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const python = process.env.NEXUS_PYTHON ?? resolve(root, ".venv/bin/python");
const sourcePath = resolve(root, "src");

describe.skipIf(!existsSync(python))("real HTTP model endpoint + real server", () => {
  let http: Server;
  let baseUrl: string;
  const bodies: { messages: { role: string; content?: string }[] }[] = [];

  beforeAll(async () => {
    let turn = 0;
    http = createServer((req, res) => {
      let raw = "";
      req.on("data", (c) => (raw += c));
      req.on("end", () => {
        bodies.push(JSON.parse(raw));
        const message =
          turn++ === 0
            ? { content: null, tool_calls: [{ id: "c1", type: "function", function: { name: "search_knowledge", arguments: '{"query":"postgresql dynamodb"}' } }] }
            : { content: "They chose PostgreSQL for joins and ad-hoc filters (demo_wiki: Decision record)." };
        res.setHeader("content-type", "application/json");
        res.end(JSON.stringify({ choices: [{ message }] }));
      });
    });
    await new Promise<void>((r) => http.listen(0, "127.0.0.1", r));
    baseUrl = `http://127.0.0.1:${(http.address() as AddressInfo).port}/v1`;
  });
  afterAll(() => new Promise<void>((r) => http.close(() => r())));

  it("agent over real fetch gets real tool output", async () => {
    const conn = await NexusConnection.connectStdio({
      command: python,
      args: ["-m", "nexus_mcp"],
       env: { NEXUS_DEMO_MODE: "true", NEXUS_LOG_LEVEL: "WARNING", PYTHONPATH: sourcePath },
    });
    try {
      const run = await runAgent({
        tools: conn,
        model: new OpenAICompatModel({ baseUrl, model: "stub" }),
        question: "Why PostgreSQL?",
      });
      expect(run.answer).toContain("PostgreSQL");
      const toolMsg = bodies[1]!.messages.find((m) => m.role === "tool");
      expect(toolMsg?.content).toContain("wiki-002"); // the server's real search output reached the model
    } finally {
      await conn.close();
    }
  }, 30_000);

  it("CLI prints the answer and exits 0", async () => {
    const cli = resolve(root, "clients/ts/dist/cli.js");
    if (!existsSync(cli)) return; // requires `npm run build`
    const { stdout } = await promisify(execFile)(
      "node",
      [cli, "Why PostgreSQL?", "--provider", "openai", "--base-url", baseUrl, "--model", "stub", "--demo",
       "--", python, "-m", "nexus_mcp"],
      {
        env: { ...process.env, NEXUS_LOG_LEVEL: "WARNING", PYTHONPATH: sourcePath },
        timeout: 30_000,
      },
    );
    expect(stdout).toContain("PostgreSQL");
  }, 40_000);
});
