import type { ChatModel, CompletionRequest, Fetch, ModelTurn, ToolCall } from "../types.js";

/**
 * Any OpenAI-compatible /chat/completions endpoint: Ollama (local), Gemini's OpenAI-compatible
 * endpoint, OpenRouter, Groq, vLLM, LM Studio, ...
 */
export interface OpenAICompatConfig {
  baseUrl: string;
  model: string;
  /** Optional: local servers such as Ollama need no key. */
  apiKey?: string;
  fetch?: Fetch;
}

type WireMessage =
  | { role: "system" | "user"; content: string }
  | {
      role: "assistant";
      content: string | null;
      tool_calls?: { id: string; type: "function"; function: { name: string; arguments: string } }[];
    }
  | { role: "tool"; tool_call_id: string; content: string };

function toWire(req: CompletionRequest): WireMessage[] {
  const out: WireMessage[] = [{ role: "system", content: req.system }];
  for (const m of req.messages) {
    if (m.role === "user") out.push({ role: "user", content: m.content });
    else if (m.role === "assistant") {
      out.push({
        role: "assistant",
        content: m.content || null,
        ...(m.toolCalls.length > 0
          ? {
              tool_calls: m.toolCalls.map((c) => ({
                id: c.id,
                type: "function" as const,
                function: { name: c.name, arguments: JSON.stringify(c.arguments) },
              })),
            }
          : {}),
      });
    } else {
      for (const r of m.results) out.push({ role: "tool", tool_call_id: r.callId, content: r.content });
    }
  }
  return out;
}

export function parseArguments(raw: unknown): Record<string, unknown> {
  if (typeof raw === "object" && raw !== null && !Array.isArray(raw)) return raw as Record<string, unknown>;
  if (typeof raw !== "string" || raw.trim() === "") return {};
  try {
    const parsed: unknown = JSON.parse(raw);
    return typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return { __invalid_json__: raw.slice(0, 200) }; // surfaces as a validation error from the server
  }
}

export class OpenAICompatModel implements ChatModel {
  readonly name: string;
  private readonly doFetch: Fetch;

  constructor(private readonly cfg: OpenAICompatConfig) {
    this.name = `openai-compat:${cfg.model}`;
    this.doFetch = cfg.fetch ?? fetch;
  }

  async complete(req: CompletionRequest): Promise<ModelTurn> {
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (this.cfg.apiKey) headers.authorization = `Bearer ${this.cfg.apiKey}`;
    const res = await this.doFetch(`${this.cfg.baseUrl.replace(/\/+$/, "")}/chat/completions`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        model: this.cfg.model,
        messages: toWire(req),
        tools: req.tools.map((t) => ({
          type: "function",
          function: { name: t.name, description: t.description, parameters: t.inputSchema },
        })),
      }),
    });
    if (!res.ok) throw new Error(`Model API error: HTTP ${res.status}`);
    const data = (await res.json()) as {
      choices?: {
        message?: {
          content?: string | null;
          tool_calls?: { id?: string; function?: { name?: string; arguments?: unknown } }[];
        };
      }[];
    };
    const msg = data.choices?.[0]?.message;
    const toolCalls: ToolCall[] = (msg?.tool_calls ?? []).map((c, i) => ({
      id: c.id ?? `call_${i}`,
      name: c.function?.name ?? "",
      arguments: parseArguments(c.function?.arguments),
    }));
    return { text: msg?.content ?? "", toolCalls };
  }
}
