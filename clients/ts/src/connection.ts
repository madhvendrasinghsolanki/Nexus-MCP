import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport, getDefaultEnvironment } from "@modelcontextprotocol/sdk/client/stdio.js";
import type { ToolProvider, ToolSpec } from "./types.js";

export interface StdioServerConfig {
  command: string;
  args: string[];
  /** Extra environment for the server process (merged over a safe default set). */
  env?: Record<string, string>;
  cwd?: string;
}

/** Thin, typed wrapper over the official MCP SDK client (stdio transport). */
export class NexusConnection implements ToolProvider {
  private constructor(private readonly client: Client) {}

  static async connectStdio(cfg: StdioServerConfig): Promise<NexusConnection> {
    const transport = new StdioClientTransport({
      command: cfg.command,
      args: cfg.args,
      cwd: cfg.cwd,
      env: { ...getDefaultEnvironment(), ...(cfg.env ?? {}) },
      stderr: "inherit", // server logs go to our stderr; stdout stays the protocol channel
    });
    const client = new Client({ name: "nexus-client", version: "0.1.0" });
    await client.connect(transport);
    return new NexusConnection(client);
  }

  async listTools(): Promise<ToolSpec[]> {
    const specs: ToolSpec[] = [];
    let cursor: string | undefined;
    do {
      const page = await this.client.listTools(cursor ? { cursor } : undefined);
      for (const t of page.tools) {
        specs.push({
          name: t.name,
          description: t.description ?? "",
          inputSchema: t.inputSchema as Record<string, unknown>,
          readOnly: t.annotations?.readOnlyHint === true,
          destructive: t.annotations?.destructiveHint === true,
        });
      }
      cursor = page.nextCursor;
    } while (cursor);
    return specs;
  }

  async callTool(name: string, args: Record<string, unknown>): Promise<{ text: string; isError: boolean }> {
    const res = await this.client.callTool({ name, arguments: args });
    const content = Array.isArray(res.content) ? res.content : [];
    const text = content
      .map((c) => (c && typeof c === "object" && "text" in c ? String((c as { text: unknown }).text) : ""))
      .filter(Boolean)
      .join("\n");
    return { text, isError: res.isError === true };
  }

  async close(): Promise<void> {
    await this.client.close();
  }
}
