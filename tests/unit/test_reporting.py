from __future__ import annotations

import json
from pathlib import Path

from redline.models.testcase import TestCase
from redline.reporting.markdown import render_test_plan_markdown, write_test_plan_markdown
from redline.reporting.report import EndpointRunResult, RunReport, write_json_report
from redline.validation.validator import ValidationIssue


def _case(test_id="TC-001") -> TestCase:
    return TestCase.model_validate(
        {
            "test_id": test_id,
            "endpoint": "/tasks/{task_id}",
            "method": "GET",
            "category": "positive",
            "name": "get task",
            "purpose": "verify retrieval",
            "request": {"path_params": {"task_id": "1"}},
            "expected_status": 200,
            "assertions": [{"type": "status_code", "expected": 200}],
        }
    )


def test_render_markdown_includes_table_and_rejections():
    valid = {("GET", "/tasks/{task_id}"): [_case()]}
    issue = ValidationIssue(test_id="TC-002", message="bad thing")
    rejected = {("GET", "/tasks/{task_id}"): [issue]}
    md = render_test_plan_markdown("Task API", "1.0.0", valid, rejected)
    assert "GET /tasks/{task_id}" in md
    assert "TC-001" in md
    assert "bad thing" in md


def test_render_markdown_endpoint_with_no_valid_cases():
    md = render_test_plan_markdown("Task API", "1.0.0", {}, {("GET", "/x"): []})
    assert "No test cases passed validation" in md


def test_write_test_plan_markdown_creates_file(tmp_path: Path):
    path = write_test_plan_markdown("# hello", tmp_path)
    assert path.exists()
    assert path.read_text() == "# hello"


def test_run_report_aggregation_properties():
    report = RunReport(
        run_id="abc",
        spec_title="Task API",
        spec_version="1.0.0",
        started_at="2024-01-01T00:00:00Z",
        llm_provider="openai",
        model="gpt-4o-mini",
        max_retries=2,
        endpoints=[
            EndpointRunResult(
                endpoint="/tasks/{task_id}", method="GET",
                generated=3, validated=2, rejected=1, duplicates_removed=1,
                executed=2, passed=1, failed=1,
            )
        ],
    )
    assert report.total_generated == 3
    assert report.total_validated == 2
    assert report.total_rejected == 1
    assert report.total_passed == 1
    assert report.total_failed == 1
    assert report.endpoints_covered == 1


def test_write_json_report_round_trips(tmp_path: Path):
    report = RunReport(
        run_id="abc123",
        spec_title="Task API",
        spec_version="1.0.0",
        started_at="2024-01-01T00:00:00Z",
        llm_provider="openai",
        model="gpt-4o-mini",
        max_retries=2,
        final_status="PASSED",
    )
    path = write_json_report(report, tmp_path)
    assert path.name == "run-abc123.json"
    data = json.loads(path.read_text())
    assert data["run_id"] == "abc123"
    assert data["final_status"] == "PASSED"
