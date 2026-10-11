/** Provider-neutral shapes shared by the agent loop and every model adapter. */

export interface ToolSpec {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  /** From MCP annotations. Absent annotations are treated as NOT read-only (fail closed). */
  readOnly: boolean;
  destructive: boolean;
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface ToolResult {
  callId: string;
  name: string;
  content: string;
  isError: boolean;
}

export type ChatMessage =
  | { role: "user"; content: string }
  | { role: "assistant"; content: string; toolCalls: ToolCall[] }
  | { role: "tool"; results: ToolResult[] };

export interface ModelTurn {
  text: string;
  toolCalls: ToolCall[];
}

export interface CompletionRequest {
  system: string;
  messages: ChatMessage[];
  tools: ToolSpec[];
}

export interface ChatModel {
  readonly name: string;
  complete(req: CompletionRequest): Promise<ModelTurn>;
}

/** What the agent needs from an MCP server. Implemented by NexusConnection and by test fakes. */
export interface ToolProvider {
  listTools(): Promise<ToolSpec[]>;
  callTool(name: string, args: Record<string, unknown>): Promise<{ text: string; isError: boolean }>;
}

export type Fetch = typeof fetch;
