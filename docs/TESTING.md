# Testing strategy

Run everything: `make check` (ruff, mypy --strict, pytest) and `make client-test` (TypeScript).
Coverage gate: `make coverage` fails below 95% (line + branch); CI enforces the same.

## Layers

| Layer | Where | What it proves |
|---|---|---|
| Unit | `tests/unit/` | Pure logic: filters, validation, sanitizing, ranking, write guard, conversion, config |
| Provider | `tests/unit/test_*_provider.py` | Each provider against a stateful HTTP fake (`tests/support/`) |
| Integration | `tests/integration/` | Real MCP `Client` against the real server: in-process, stdio subprocess and HTTP |
| Contract | `tests/integration/test_tool_surface_contract.py` | Tool names, schemas and annotations match `tests/snapshots/tool_surface.json` |
| Property-based | `tests/unit/test_properties.py` | Invariants over generated input (hypothesis) |
| Live (opt-in) | `tests/live/` | Real Notion/Supabase read-only smoke checks |
| TypeScript | `clients/ts/test/` | Agent loop and policy, model adapters, end-to-end against the Python server |

## Fakes, not mocks
Providers are tested through `httpx.MockTransport` emulators that keep state (rows, pages, versions),
so a test exercises the same request building, retry and error mapping as production. Nothing
asserts on call counts of internal functions.

## Property-based tests
Security-critical pure functions are checked over generated input rather than hand-picked cases:
- PostgREST `in.(...)` values and `quote()` round-trip, so no value can break out of its quotes.
- Filter column names are always plain identifiers; LIKE terms never keep unescaped wildcards.
- `sanitize_*` is idempotent; `clip_json` bounds every string.
- `parse_notion_id` round-trips every accepted format and never returns a non-UUID.
- `rank` is deterministic, de-duplicated, bounded to [0,1] and sorted.
- A token bucket never grants more than capacity plus elapsed refill.

## Contract snapshot
Tool schemas are the server's public API. The snapshot covers three modes (read-only, writes,
writes+deletes) and asserts: write tools are not advertised when writes are off, delete tools need
their own flag, and every mutating tool defaults to `dry_run=true`. Intentional change:
`make snapshots`, then review the JSON diff.

## Mutation checks (manual)
Tests were validated by breaking the code on purpose and confirming a failure: dropping quote
escaping, flipping a `dry_run` default, removing the write confirmation, the Notion version check
and the TypeScript read-only filter. Not automated: a full mutation tool run is not part of CI.

## Live tests
`NEXUS_LIVE_TESTS=1 NOTION_TOKEN=... SUPABASE_URL=... SUPABASE_KEY=... make live`. They force writes
off, never run in CI, and were **not run during development of this project** (no credentials in the
build environment), so treat the provider-vs-real-API behavior as checked only against fakes.

## Known gaps
- Fakes encode our understanding of the Notion/Supabase APIs; only the live tests can contradict it.
- The prompt-injection scanner is heuristic; tests prove it flags known patterns, not that it catches all.
- `__main__` is exercised by a subprocess test, which coverage does not measure.
- Roughly 3% of lines are uncovered (defensive branches in the providers).
