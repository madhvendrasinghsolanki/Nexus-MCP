import type { ChatModel, Fetch } from "../types.js";
import { AnthropicModel } from "./anthropic.js";
import { OpenAICompatModel } from "./openai.js";

export interface ModelSelection {
  provider: "anthropic" | "openai";
  model: string;
  baseUrl?: string;
  /** Name of the environment variable holding the API key (the key itself is never an argument). */
  apiKeyEnv?: string;
  env?: NodeJS.ProcessEnv;
  fetch?: Fetch;
}

export const DEFAULT_MODELS = { anthropic: "claude-sonnet-5-5", openai: "llama3.1" } as const;

export function createModel(sel: ModelSelection): ChatModel {
  const env = sel.env ?? process.env;
  if (sel.provider === "anthropic") {
    const keyVar = sel.apiKeyEnv ?? "ANTHROPIC_API_KEY";
    const apiKey = env[keyVar];
    if (!apiKey) throw new Error(`Set ${keyVar} to use the Anthropic provider.`);
    return new AnthropicModel({ apiKey, model: sel.model, baseUrl: sel.baseUrl, fetch: sel.fetch });
  }
  const apiKey = sel.apiKeyEnv ? env[sel.apiKeyEnv] : undefined;
  if (sel.apiKeyEnv && !apiKey) throw new Error(`Set ${sel.apiKeyEnv} (named by --api-key-env).`);
  return new OpenAICompatModel({
    baseUrl: sel.baseUrl ?? "http://localhost:11434/v1", // Ollama default
    model: sel.model,
    apiKey,
    fetch: sel.fetch,
  });
}
