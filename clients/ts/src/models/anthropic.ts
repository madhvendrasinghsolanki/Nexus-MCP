import type { ChatMessage, ChatModel, CompletionRequest, Fetch, ModelTurn, ToolCall } from "../types.js";

export interface AnthropicConfig {
  apiKey: string;
  model: string;
  baseUrl?: string;
  maxTokens?: number;
  fetch?: Fetch;
}

type Block =
  | { type: "text"; text: string }
  | { type: "tool_use"; id: string; name: string; input: Record<string, unknown> }
  | { type: "tool_result"; tool_use_id: string; content: string; is_error: boolean };

function toWire(messages: ChatMessage[]): { role: "user" | "assistant"; content: string | Block[] }[] {
  return messages.map((m) => {
    if (m.role === "user") return { role: "user", content: m.content };
    if (m.role === "assistant") {
      const blocks: Block[] = [];
      if (m.content) blocks.push({ type: "text", text: m.content });
      for (const c of m.toolCalls) blocks.push({ type: "tool_use", id: c.id, name: c.name, input: c.arguments });
      return { role: "assistant", content: blocks };
    }
    return {
      role: "user",
      content: m.results.map((r) => ({
        type: "tool_result" as const,
        tool_use_id: r.callId,
        content: r.content,
        is_error: r.isError,
      })),
    };
  });
}

export class AnthropicModel implements ChatModel {
  readonly name: string;
  private readonly doFetch: Fetch;

  constructor(private readonly cfg: AnthropicConfig) {
    this.name = `anthropic:${cfg.model}`;
    this.doFetch = cfg.fetch ?? fetch;
  }

  async complete(req: CompletionRequest): Promise<ModelTurn> {
    const res = await this.doFetch(`${this.cfg.baseUrl ?? "https://api.anthropic.com"}/v1/messages`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": this.cfg.apiKey,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: this.cfg.model,
        max_tokens: this.cfg.maxTokens ?? 2048,
        system: req.system,
        messages: toWire(req.messages),
        tools: req.tools.map((t) => ({ name: t.name, description: t.description, input_schema: t.inputSchema })),
      }),
    });
    if (!res.ok) {
      // Never echo the response body into logs/errors blindly; status is enough to act on.
      throw new Error(`Anthropic API error: HTTP ${res.status}`);
    }
    const data = (await res.json()) as { content?: Block[] };
    let text = "";
    const toolCalls: ToolCall[] = [];
    for (const b of data.content ?? []) {
      if (b.type === "text") text += b.text;
      else if (b.type === "tool_use") toolCalls.push({ id: b.id, name: b.name, arguments: b.input ?? {} });
    }
    return { text, toolCalls };
  }
}
