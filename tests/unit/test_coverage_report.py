from __future__ import annotations

import json
from pathlib import Path

from scripts.coverage_report import build_report, function_entry_coverage_proxy, markdown
from scripts.coverage_report import test_counts as count_junit_tests


def test_function_entry_proxy_is_explicitly_supplemental(tmp_path: Path) -> None:
    source_root = tmp_path / "src" / "nexus_mcp"
    source_root.mkdir(parents=True)
    source = source_root / "example.py"
    source.write_text(
        "def covered():\n    return 1\n\ndef skipped():\n    return 2\n",
        encoding="utf-8",
    )
    data = {"files": {str(source): {"executed_lines": [2]}}}
    assert function_entry_coverage_proxy(data, source_root) == (1, 2, 50.0)


def test_build_report_and_markdown_include_all_quality_metrics(tmp_path: Path) -> None:
    source_root = tmp_path / "src" / "nexus_mcp"
    source_root.mkdir(parents=True)
    (source_root / "example.py").write_text("def covered():\n    return 1\n", encoding="utf-8")
    coverage = tmp_path / "coverage.json"
    coverage.write_text(
        json.dumps(
            {
                "files": {str(source_root / "example.py"): {"executed_lines": [2]}},
                "totals": {
                    "covered_lines": 1,
                    "num_statements": 1,
                    "percent_statements_covered": 100.0,
                    "num_branches": 2,
                    "covered_branches": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    junit = tmp_path / "pytest.xml"
    junit.write_text(
        '<testsuite tests="3" failures="1" errors="0" skipped="1" />', encoding="utf-8"
    )

    report = build_report(coverage, junit, source_root)
    assert report["tests"] == {"total": 3, "passed": 1, "failed": 1, "skipped": 1}
    assert report["coverage"]["branches"]["percent"] == 50.0
    assert report["coverage"]["functions"]["available"] is False
    text = markdown(report)
    assert "Lines" in text and "Branches" in text and "Functions" in text
    assert "N/A" in text
    assert count_junit_tests(junit)["passed"] == 1


def test_test_counts_aggregates_junit_suites(tmp_path: Path) -> None:
    junit = tmp_path / "pytest.xml"
    junit.write_text(
        "<testsuites>"
        '<testsuite tests="2" failures="0" errors="0" skipped="1" />'
        '<testsuite tests="3" failures="1" errors="1" skipped="0" />'
        "</testsuites>",
        encoding="utf-8",
    )

    assert count_junit_tests(junit) == {"total": 5, "passed": 2, "failed": 2, "skipped": 1}
