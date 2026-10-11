"""Turn pytest and coverage.py reports into a GitHub-friendly quality summary.

coverage.py reports executable statements/lines and branches. It does not expose a
native function coverage metric, so the report deliberately marks Functions as
unavailable. The supplemental AST proxy below is retained for diagnostics only; it
is not a coverage.py metric and is not used as a quality gate.
"""

from __future__ import annotations

import argparse
import ast
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


def _percent(covered: int, total: int) -> float:
    return round(100.0 if total == 0 else covered * 100.0 / total, 2)


def _first_executable_line(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int | None:
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr):
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            body.pop(0)
    return body[0].lineno if body else None


def function_entry_coverage_proxy(
    coverage_data: dict[str, Any], source_root: Path
) -> tuple[int, int, float]:
    """Return a supplemental first-body-line proxy, not native function coverage."""
    covered = 0
    total = 0
    root = source_root.resolve()
    for filename, file_data in coverage_data.get("files", {}).items():
        path = Path(filename)
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            path.resolve().relative_to(root)
        except ValueError:
            continue
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        executed = set(file_data.get("executed_lines", []))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            line = _first_executable_line(node)
            if line is None:
                continue
            total += 1
            covered += line in executed
    return covered, total, _percent(covered, total)


def test_counts(junit_path: Path) -> dict[str, int]:
    """Read pytest's JUnit report and return total, pass, fail, and skip counts."""
    # The file is generated locally by pytest, not supplied by a user or network.
    root = ET.parse(junit_path).getroot()  # noqa: S314
    nodes = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))

    def count(name: str) -> int:
        return sum(int(node.attrib.get(name, "0")) for node in nodes)

    total = count("tests")
    failed = count("failures") + count("errors")
    skipped = count("skipped")
    return {
        "total": total,
        "passed": total - failed - skipped,
        "failed": failed,
        "skipped": skipped,
    }


def build_report(coverage_path: Path, junit_path: Path, source_root: Path) -> dict[str, Any]:
    data = json.loads(coverage_path.read_text(encoding="utf-8"))
    totals = data["totals"]
    statements_total = int(totals["num_statements"])
    statements_covered = int(totals["covered_lines"])
    branches_total = int(totals.get("num_branches", 0))
    branches_covered = int(totals.get("covered_branches", 0))
    lines_covered = int(totals["covered_lines"])
    return {
        "tests": test_counts(junit_path),
        "coverage": {
            "lines": {
                "covered": lines_covered,
                "total": statements_total,
                "percent": _percent(lines_covered, statements_total),
            },
            "statements": {
                "covered": statements_covered,
                "total": statements_total,
                "percent": float(totals["percent_statements_covered"]),
            },
            "branches": {
                "covered": branches_covered,
                "total": branches_total,
                "percent": _percent(branches_covered, branches_total),
            },
            "functions": {
                "available": False,
                "covered": None,
                "total": None,
                "percent": None,
                "reason": "coverage.py does not expose native function coverage",
            },
        },
    }


def markdown(report: dict[str, Any]) -> str:
    tests = report["tests"]
    coverage = report["coverage"]
    rows = [
        (
            "Tests",
            f"{tests['passed']} passed, {tests['failed']} failed, {tests['skipped']} skipped",
        ),
        ("Lines", f"{coverage['lines']['percent']:.2f}%"),
        ("Branches", f"{coverage['branches']['percent']:.2f}%"),
        ("Functions", "N/A (coverage.py does not expose native function coverage)"),
        ("Statements", f"{coverage['statements']['percent']:.2f}%"),
    ]
    return "### Python tests and coverage\n\n| Metric | Result |\n| --- | --- |\n" + "\n".join(
        f"| {name} | {value} |" for name, value in rows
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("coverage_json", type=Path)
    parser.add_argument("--junitxml", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, default=Path("src/nexus_mcp"))
    parser.add_argument("--output-json", type=Path)
    args = parser.parse_args()

    report = build_report(args.coverage_json, args.junitxml, args.source_root)
    if args.output_json:
        args.output_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
