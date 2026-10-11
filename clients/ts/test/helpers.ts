import type { ChatModel, CompletionRequest, ModelTurn, ToolProvider, ToolSpec } from "../src/types.js";

export const spec = (name: string, readOnly: boolean): ToolSpec => ({
  name,
  description: name,
  inputSchema: { type: "object", properties: {} },
  readOnly,
  destructive: !readOnly,
});

/** Model that replays a fixed script and records every request it received. */
export class ScriptedModel implements ChatModel {
  readonly name = "scripted";
  readonly requests: CompletionRequest[] = [];
  private i = 0;
  constructor(private readonly turns: ModelTurn[]) {}
  async complete(req: CompletionRequest): Promise<ModelTurn> {
    this.requests.push(structuredClone(req));
    const turn = this.turns[Math.min(this.i++, this.turns.length - 1)];
    if (!turn) throw new Error("empty script");
    return turn;
  }
}

export class FakeTools implements ToolProvider {
  readonly calls: { name: string; args: Record<string, unknown> }[] = [];
  constructor(
    private readonly specs: ToolSpec[],
    private readonly impl: (name: string, args: Record<string, unknown>) => { text: string; isError?: boolean } = () => ({
      text: "ok",
    }),
  ) {}
  async listTools(): Promise<ToolSpec[]> {
    return this.specs;
  }
  async callTool(name: string, args: Record<string, unknown>) {
    this.calls.push({ name, args });
    const r = this.impl(name, args);
    return { text: r.text, isError: r.isError === true };
  }
}

export const call = (id: string, name: string, args: Record<string, unknown> = {}) => ({ id, name, arguments: args });
