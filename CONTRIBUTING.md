# Contributing

```bash
uv venv && uv pip install -e ".[dev]"
make check        # ruff + mypy --strict + pytest
make coverage     # must stay >= 95%
make client-test  # TypeScript client
make evals-selftest
```

- **New data source:** implement `providers.base.DataSource`, add a credential-gated factory in
  `providers/registry.py`, and test it against an `httpx.MockTransport` fake (see `tests/support/`).
- **New tool:** put it in the matching `tools/` module behind `guarded(...)`; mutating tools need
  `Audit(...)`, `dry_run=True` by default and a write-guard check. Then run `make snapshots` and
  review the diff of `tests/snapshots/tool_surface.json` (it is the public API).
- **Never** put secrets in code, tests or logs; tests use obviously fake values.
- Gate commits on `ruff check . && mypy && pytest` passing.
