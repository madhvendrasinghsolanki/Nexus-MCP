import { describe, expect, it } from "vitest";
import { AnthropicModel } from "../src/models/anthropic.js";
import { createModel } from "../src/models/index.js";
import { OpenAICompatModel, parseArguments } from "../src/models/openai.js";
import type { CompletionRequest, Fetch } from "../src/types.js";
import { spec } from "./helpers.js";

const req: CompletionRequest = {
  system: "sys",
  tools: [spec("search_knowledge", true)],
  messages: [
    { role: "user", content: "hi" },
    { role: "assistant", content: "searching", toolCalls: [{ id: "c1", name: "search_knowledge", arguments: { query: "a" } }] },
    { role: "tool", results: [{ callId: "c1", name: "search_knowledge", content: "result", isError: true }] },
  ],
};

function mockFetch(body: unknown, status = 200) {
  const seen: { url: string; init: RequestInit }[] = [];
  const f: Fetch = async (url, init) => {
    seen.push({ url: String(url), init: init ?? {} });
    return new Response(JSON.stringify(body), { status });
  };
  return { f, seen };
}

describe("Anthropic adapter", () => {
  it("builds the Messages request and parses tool_use", async () => {
    const { f, seen } = mockFetch({
      content: [
        { type: "text", text: "Let me look." },
        { type: "tool_use", id: "tu1", name: "search_knowledge", input: { query: "atlas" } },
      ],
    });
    const turn = await new AnthropicModel({ apiKey: "sk-test", model: "m", fetch: f }).complete(req);
    expect(turn).toEqual({
      text: "Let me look.",
      toolCalls: [{ id: "tu1", name: "search_knowledge", arguments: { query: "atlas" } }],
    });
    const sent = seen[0]!;
    expect(sent.url).toBe("https://api.anthropic.com/v1/messages");
    expect((sent.init.headers as Record<string, string>)["x-api-key"]).toBe("sk-test");
    const body = JSON.parse(String(sent.init.body));
    expect(body.system).toBe("sys");
    expect(body.tools[0].input_schema).toBeDefined();
    expect(body.messages[1].content[1]).toMatchObject({ type: "tool_use", id: "c1" });
    expect(body.messages[2].content[0]).toMatchObject({ type: "tool_result", tool_use_id: "c1", is_error: true });
  });

  it("reports HTTP errors without leaking the key or body", async () => {
    const { f } = mockFetch({ error: "secret sk-test echoed" }, 401);
    const err = await new AnthropicModel({ apiKey: "sk-test", model: "m", fetch: f }).complete(req).catch((e) => e);
    expect(String(err)).toContain("HTTP 401");
    expect(String(err)).not.toContain("sk-test");
  });
});

describe("OpenAI-compatible adapter", () => {
  it("builds chat/completions request and parses tool_calls", async () => {
    const { f, seen } = mockFetch({
      choices: [{ message: { content: null, tool_calls: [{ id: "x", function: { name: "get_item", arguments: '{"source":"s","id":"1"}' } }] } }],
    });
    const turn = await new OpenAICompatModel({ baseUrl: "http://localhost:11434/v1/", model: "llama3.1", fetch: f }).complete(req);
    expect(turn.toolCalls).toEqual([{ id: "x", name: "get_item", arguments: { source: "s", id: "1" } }]);
    expect(seen[0]!.url).toBe("http://localhost:11434/v1/chat/completions");
    expect((seen[0]!.init.headers as Record<string, string>).authorization).toBeUndefined();
    const body = JSON.parse(String(seen[0]!.init.body));
    expect(body.messages[0]).toEqual({ role: "system", content: "sys" });
    expect(body.messages[2].tool_calls[0].function.arguments).toBe('{"query":"a"}');
    expect(body.messages[3]).toEqual({ role: "tool", tool_call_id: "c1", content: "result" });
    expect(body.tools[0].function.parameters).toBeDefined();
  });

  it("sends a bearer key only when configured", async () => {
    const { f, seen } = mockFetch({ choices: [{ message: { content: "hi" } }] });
    const turn = await new OpenAICompatModel({ baseUrl: "https://x/v1", model: "m", apiKey: "k1", fetch: f }).complete(req);
    expect(turn).toEqual({ text: "hi", toolCalls: [] });
    expect((seen[0]!.init.headers as Record<string, string>).authorization).toBe("Bearer k1");
  });

  it("tolerates malformed model output", () => {
    expect(parseArguments('{"a":1}')).toEqual({ a: 1 });
    expect(parseArguments({ a: 1 })).toEqual({ a: 1 });
    expect(parseArguments("")).toEqual({});
    expect(parseArguments("[1,2]")).toEqual({});
    expect(parseArguments("{broken")).toHaveProperty("__invalid_json__");
  });
});

describe("createModel", () => {
  it("requires the named API key variable", () => {
    expect(() => createModel({ provider: "anthropic", model: "m", env: {} })).toThrow(/ANTHROPIC_API_KEY/);
    expect(() => createModel({ provider: "openai", model: "m", apiKeyEnv: "GEMINI_KEY", env: {} })).toThrow(/GEMINI_KEY/);
    expect(createModel({ provider: "openai", model: "m", env: {} }).name).toBe("openai-compat:m");
    expect(createModel({ provider: "anthropic", model: "m", env: { ANTHROPIC_API_KEY: "k" } }).name).toBe("anthropic:m");
  });
});
