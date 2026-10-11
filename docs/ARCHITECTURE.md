# Architecture

Nexus MCP uses a ports-and-adapters layout so each data source is a plug-in.

```
MCP client ──stdio | streamable-http──▶ server.py (create_server)
                                           │ registers
                                  tools/  (thin, typed handlers; safe_tool error boundary)
                                           │ depends on
                       services/ (health, search, write-guard)     core/ (config, errors, logging, models)
                                           │ depends on
                       providers/base.DataSource  ◀── registry ◀── factories(settings)
                              ├─ supabase
                              ├─ notion
                              └─ demo (fictional sample data)
```

## Design decisions
- **Provider port (`DataSource`)**: tools never import a vendor SDK; adding a source is one class plus one factory.
- **Credential-gated providers**: a provider registers only if its env vars exist, so the server runs with 0, 1 or 2 sources.
- **Structured I/O**: every tool returns a Pydantic model, so clients receive a JSON output schema and typed content.
- **Error boundary (`safe_tool`)**: `NexusError` becomes a stable `[code] message Hint:`; anything else is logged with a traceback and returned as a generic `internal_error`. Upstream bodies and secrets never reach the client.
- **stdout is the wire**: all logging goes to stderr (verified by a real subprocess test) with key- and pattern-based secret redaction.
- **Safe by default**: `NEXUS_ALLOW_WRITES` and `NEXUS_ALLOW_DELETES` default to false; deletes require writes. Dry-run, confirmation and audit rules sit on top (see Security hardening).

## Tools
| Group | Tools |
|---|---|
| System | `server_info`, `list_sources`, `health_check` |
| Supabase | `supabase_list_tables`, `supabase_describe_table`, `supabase_query`, `supabase_insert`, `supabase_update`, `supabase_delete` |
| Notion | `notion_search`, `notion_get_page`, `notion_get_database`, `notion_query_database`, `notion_create_page`, `notion_update_page`, `notion_append_content`, `notion_archive_page` |
| Unified | `search_knowledge` (ranked cross-source), `get_item`, resource `knowledge://{source}/{item_id}`, prompt `research_topic` |

## Supabase provider
- **Transport**: a thin async PostgREST client (httpx) instead of a heavy SDK: fewer dependencies, fully testable with `httpx.MockTransport`.
- **No raw query strings**: filters, ordering and columns are validated models (`Filter`, `OrderBy`); identifiers are regex-checked, reserved PostgREST parameter names are refused, and `in` values are quoted.
- **Access policy**: `SUPABASE_ALLOWED_TABLES` limits reads; `SUPABASE_WRITABLE_TABLES` limits writes. Writes also need `NEXUS_ALLOW_WRITES`, and deletes need `NEXUS_ALLOW_DELETES`.
- **Write tools are hidden, not just blocked**: with writes off, `supabase_insert/update/delete` are not advertised at all.
- **Two-step destructive writes**: update/delete default to `dry_run=true`. Applying requires `expected_count` equal to the dry run's `matched_count`; the server re-counts and refuses on mismatch. A `NEXUS_MAX_WRITE_ROWS` budget caps blast radius. Filters are mandatory.
- **Schema discovery** via the PostgREST OpenAPI document, with a sample-row fallback when the key cannot read it.
- **Errors**: HTTP/PostgREST errors map to `NexusError` codes; keys, URLs and headers are never echoed.

## Notion provider
- **API version pinned** (`NOTION_API_VERSION`, default `2022-06-28`), configurable. Notion states it has no plans to retire older versions; newer versions split databases into data sources, which this stage does not use.
- **Typed filters without Notion JSON**: callers send `{property, op, value}`; the server fetches the database schema and maps it to the property's type (text, number, select/status, multi-select, date, checkbox, people, relation), rejecting invalid combinations with the list of valid operators.
- **Plain values for writes**: properties are written as plain values and converted per schema type; unsupported types (relation, people, files, formula, rollup) are refused explicitly.
- **Scoped writes**: a page may be written only if its direct parent (or the page itself) is in `NOTION_WRITABLE_PARENTS`. Archive goes to the Notion trash and is restorable.
- **Optimistic concurrency**: update/archive dry runs return `last_edited_time`; applying requires echoing it, so a page edited in between is refused. This mirrors the row-count confirmation used for Supabase.
- **Untrusted content**: tool descriptions tell the model that returned page text is data, not instructions (prompt-injection mitigation; see Security hardening for the rest).
- **ID handling**: IDs and notion.so/notion.site URLs are parsed to a canonical UUID with word-boundary matching (slugs such as `My-Page-<id>` contain hex letters), so nothing user-controlled reaches a request path.
- **Retries**: reads and read-only POSTs (search, query) retry once on 429/5xx honouring `Retry-After`; writes are never retried.
- **Logging**: `httpx` request logs are silenced because URLs carry user query data.

## Unified knowledge
- **`search_knowledge`** fans out to every searchable source concurrently, each bounded by `NEXUS_REQUEST_TIMEOUT_S`. A failing or slow source never sinks the call: the response carries per-source status (`ok`, `returned`, `latency_ms`, safe `error`) and `partial=true`. A total failure inside a provider is raised, not hidden as "no results".
- **Ranking** (`services/ranking.py`) is deliberately simple and explainable: title token coverage, snippet coverage and exact-phrase bonus, normalised to 0..1 with a small floor for source-reported hits. A source with no snippet gets its weight moved to the title so Notion title hits are not capped below Supabase hits. Ties break on recency, then provider order; duplicates collapse by URL or `source:id`.
- **`get_item`** returns the full document for an id from search: a Notion page (properties + markdown) or database schema, or a Supabase row (`table:id`).
- **MCP resource** `knowledge://{source}/{item_id}` and **prompt** `research_topic` expose the same data through the other two MCP primitives; the prompt tells the model to cite sources, report partial results and ignore instructions inside retrieved text.
- **Safe defaults for embedders**: logging defaults to stderr even if the host never configures it, so an embedded server cannot corrupt a stdio stream.

## Clients
- **Demo mode** (`NEXUS_DEMO_MODE`) registers two fictional in-memory sources. It makes every client, test and eval runnable with zero credentials, and it carries a labelled prompt-injection fixture for safety evaluation.
- **TypeScript client** (`clients/ts`): official MCP SDK over stdio, plus two thin model adapters (Anthropic Messages, any OpenAI-compatible chat API) behind one `ChatModel` interface. The agent loop is provider-independent and unit-tested with scripted models, then integration-tested against the real Python server.
- **Client-side guardrails** complement the server's: read-only tools only by default, per-call human confirmation for writes, refusal of unlisted tools, output truncation, step limit, and an untrusted-content system prompt.
- Setup guides for Claude Desktop/Code, Cursor, VS Code and Gemini CLI are in `docs/CLIENTS.md`; each format was checked against that client's documentation (VS Code `env`/`inputs` and the Gemini endpoint URL are flagged as unverified).

## Security hardening
Full threat model, residual risks and operator checklist: `docs/SECURITY.md`. Structure:
- **One boundary for every tool** (`guarded`): rate limit, timeout, audit, then error translation. A new tool cannot skip it without not using the decorator, and the integration tests exercise it per tool family.
- **Fail-closed audit**: for real writes the intent is persisted first; no durable record, no write.
- **Sanitize by type**: external text fields are typed `SafeStr`/`SafeDict`/`SafeRows`, so cleaning happens at model construction instead of at each call site, and a structural test fails if a new text field is added without it.
- **Transport**: auth middleware wraps the SDK's transport security; non-loopback binds need a token, including when selected by CLI flag.
