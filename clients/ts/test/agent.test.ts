import { describe, expect, it } from "vitest";
import { runAgent } from "../src/agent.js";
import { FakeTools, ScriptedModel, call, spec } from "./helpers.js";

const tools = () =>
  new FakeTools([spec("search_knowledge", true), spec("get_item", true), spec("supabase_delete", false)]);

describe("agent loop", () => {
  it("runs tools then returns the final answer", async () => {
    const t = tools();
    const model = new ScriptedModel([
      { text: "", toolCalls: [call("1", "search_knowledge", { query: "atlas" })] },
      { text: "Atlas ships Nov 15 (wiki: roadmap).", toolCalls: [] },
    ]);
    const run = await runAgent({ tools: t, model, question: "when does atlas ship?" });
    expect(run.stopped).toBe("final");
    expect(run.steps).toBe(2);
    expect(run.answer).toContain("Nov 15");
    expect(t.calls).toEqual([{ name: "search_knowledge", args: { query: "atlas" } }]);
    // the tool result was fed back to the model
    const last = model.requests[1]!.messages.at(-1)!;
    expect(last.role).toBe("tool");
  });

  it("hides write tools from the model by default", async () => {
    const model = new ScriptedModel([{ text: "done", toolCalls: [] }]);
    await runAgent({ tools: tools(), model, question: "q" });
    expect(model.requests[0]!.tools.map((x) => x.name)).toEqual(["search_knowledge", "get_item"]);
  });

  it("refuses a hallucinated or hidden tool call instead of executing it", async () => {
    const t = tools();
    const model = new ScriptedModel([
      { text: "", toolCalls: [call("1", "supabase_delete", { table: "notes" })] },
      { text: "I could not do that.", toolCalls: [] },
    ]);
    const events: string[] = [];
    await runAgent({ tools: t, model, question: "delete everything", onEvent: (e) => events.push(e.type) });
    expect(t.calls).toHaveLength(0);
    const fed = model.requests[1]!.messages.at(-1)!;
    expect(fed.role === "tool" && fed.results[0]!.isError).toBe(true);
  });

  it("with allowWrites, still denies write tools unless the user confirms", async () => {
    const t = tools();
    const turns = [
      { text: "", toolCalls: [call("1", "supabase_delete", { table: "notes" })] },
      { text: "ok", toolCalls: [] },
    ];
    await runAgent({ tools: t, model: new ScriptedModel(turns), question: "q", policy: { allowWrites: true } });
    expect(t.calls).toHaveLength(0); // no confirm callback -> denied

    await runAgent({
      tools: t,
      model: new ScriptedModel(turns),
      question: "q",
      policy: { allowWrites: true, confirm: async () => false },
    });
    expect(t.calls).toHaveLength(0);

    await runAgent({
      tools: t,
      model: new ScriptedModel(turns),
      question: "q",
      policy: { allowWrites: true, confirm: async (c) => c.name === "supabase_delete" },
    });
    expect(t.calls).toHaveLength(1);
  });

  it("stops at maxSteps when the model never finishes", async () => {
    const model = new ScriptedModel([{ text: "", toolCalls: [call("1", "search_knowledge", { query: "x" })] }]);
    const run = await runAgent({ tools: tools(), model, question: "q", maxSteps: 3 });
    expect(run).toMatchObject({ stopped: "max_steps", steps: 3 });
    expect(run.toolCalls).toHaveLength(3);
  });

  it("truncates huge tool output and survives tool exceptions", async () => {
    const big = new FakeTools([spec("search_knowledge", true)], () => ({ text: "x".repeat(500) }));
    const model = new ScriptedModel([
      { text: "", toolCalls: [call("1", "search_knowledge")] },
      { text: "fine", toolCalls: [] },
    ]);
    await runAgent({ tools: big, model, question: "q", maxToolChars: 100 });
    const fed = model.requests[1]!.messages.at(-1)!;
    expect(fed.role === "tool" && fed.results[0]!.content).toContain("[truncated 400 characters]");

    const boom = new FakeTools([spec("search_knowledge", true)], () => {
      throw new Error("pipe closed");
    });
    const model2 = new ScriptedModel([
      { text: "", toolCalls: [call("1", "search_knowledge")] },
      { text: "recovered", toolCalls: [] },
    ]);
    const run = await runAgent({ tools: boom, model: model2, question: "q" });
    expect(run.answer).toBe("recovered");
    expect(run.toolCalls[0]!.isError).toBe(true);
  });

  it("treats tools without annotations as write tools (fail closed)", async () => {
    const t = new FakeTools([{ ...spec("mystery", true), readOnly: false }]);
    const model = new ScriptedModel([{ text: "ok", toolCalls: [] }]);
    await runAgent({ tools: t, model, question: "q" });
    expect(model.requests[0]!.tools).toHaveLength(0);
  });

  it("system prompt warns that tool output is untrusted", async () => {
    const model = new ScriptedModel([{ text: "ok", toolCalls: [] }]);
    await runAgent({ tools: tools(), model, question: "q" });
    expect(model.requests[0]!.system).toMatch(/untrusted/i);
  });
});
