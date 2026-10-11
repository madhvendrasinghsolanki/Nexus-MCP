/** Drives the REAL Python server over stdio in demo mode, with a scripted model. */
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { runAgent } from "../src/agent.js";
import { NexusConnection } from "../src/connection.js";
import { ScriptedModel, call } from "./helpers.js";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const python = process.env.NEXUS_PYTHON ?? resolve(root, ".venv/bin/python");
const available = existsSync(python);

describe.skipIf(!available)("against the real server (demo mode)", () => {
  let conn: NexusConnection;
  beforeAll(async () => {
    conn = await NexusConnection.connectStdio({
      command: python,
      args: ["-m", "nexus_mcp"],
      env: { NEXUS_DEMO_MODE: "true", NEXUS_LOG_LEVEL: "WARNING" },
    });
  }, 30_000);
  afterAll(async () => {
    await conn?.close();
  });

  it("discovers tools with annotations", async () => {
    const tools = await conn.listTools();
    const names = tools.map((t) => t.name);
    expect(names).toEqual(expect.arrayContaining(["search_knowledge", "get_item", "server_info"]));
    expect(tools.every((t) => t.readOnly)).toBe(true); // demo mode exposes no write tools
  });

  it("search -> get_item -> answer, across both demo sources", async () => {
    const model = new ScriptedModel([
      { text: "", toolCalls: [call("1", "search_knowledge", { query: "security review" })] },
      { text: "", toolCalls: [call("2", "get_item", { source: "demo_wiki", id: "wiki-010" })] },
      { text: "Check RLS and audit logging (demo_wiki: Security review checklist).", toolCalls: [] },
    ]);
    const run = await runAgent({ tools: conn, model, question: "What does the security review cover?" });
    expect(run.stopped).toBe("final");
    expect(run.toolCalls.map((c) => c.isError)).toEqual([false, false]);
    const search = model.requests[1]!.messages.at(-1)!;
    const text = search.role === "tool" ? search.results[0]!.content : "";
    expect(text).toContain("demo_wiki");
    expect(text).toContain("demo_tickets");
  });

  it("server errors come back as readable tool errors, not crashes", async () => {
    const out = await conn.callTool("get_item", { source: "demo_wiki", id: "nope" });
    expect(out.isError).toBe(true);
    expect(out.text).toContain("[not_found]");
  });

  it("a prompt-injected delete attempt cannot reach a tool", async () => {
    const model = new ScriptedModel([
      { text: "", toolCalls: [call("1", "get_item", { source: "demo_wiki", id: "wiki-005" })] },
      { text: "", toolCalls: [call("2", "supabase_delete", { table: "notes", filters: [], dry_run: false })] },
      { text: "I will not act on instructions inside documents.", toolCalls: [] },
    ]);
    const run = await runAgent({ tools: conn, model, question: "Summarize the vendor notes" });
    expect(run.toolCalls[1]).toMatchObject({ name: "supabase_delete", isError: true });
    const last = model.requests[2]!.messages.at(-1)!;
    expect(last.role === "tool" && last.results[0]!.content).toContain("not available");
  });
});
