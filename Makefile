.PHONY: install lint type test test-unit test-integration coverage snapshots live check run client-test demo evals-selftest evals
install:
	uv venv -q .venv && . .venv/bin/activate && uv pip install -e ".[dev]"
lint:
	ruff check . && ruff format --check .
type:
	mypy
test:
	pytest -q
test-unit:
	pytest -q tests/unit
test-integration:
	pytest -q -m integration
coverage:
	pytest -q --cov --cov-fail-under=95
snapshots:
	UPDATE_SNAPSHOTS=1 pytest -q tests/integration/test_tool_surface_contract.py
live:
	NEXUS_LIVE_TESTS=1 pytest -q -m live
check: lint type test
run:
	nexus-mcp
client-test:
	cd clients/ts && npm install && npm run typecheck && npm run build && npm test
demo:
	NEXUS_DEMO_MODE=true . .venv/bin/activate && nexus-mcp
evals-selftest:
	cd clients/ts && npm run build && cd ../../evals && npm install --no-audit --no-fund && npm run selftest >/dev/null 2>&1; node check-selftest.mjs
evals:
	cd evals && npm run eval && npm run view
