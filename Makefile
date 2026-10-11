.PHONY: install lint type test test-unit test-integration coverage snapshots live check run client-test demo evals-selftest evals
install:
	uv venv -q .venv && . .venv/bin/activate && uv pip install -e ".[dev]"
lint:
	ruff check . && ruff format --check .
type:
	mypy
test:
	PYTHONPATH=src pytest -q
test-unit:
	PYTHONPATH=src pytest -q tests/unit
test-integration:
	PYTHONPATH=src pytest -q -m integration
coverage:
	mkdir -p reports
	PYTHONPATH=src pytest -q --cov=src/nexus_mcp --cov-branch --cov-report=term-missing --cov-report=xml:reports/coverage.xml --cov-report=json:reports/coverage.json --cov-report=html:reports/htmlcov --junitxml=reports/pytest.xml --cov-fail-under=95
	PYTHONPATH=src python scripts/coverage_report.py reports/coverage.json --junitxml reports/pytest.xml --output-json reports/quality.json
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
