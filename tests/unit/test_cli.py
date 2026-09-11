from __future__ import annotations

from click.testing import CliRunner

from redline.cli.main import cli


def test_cli_help():
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Redline" in result.output


def test_validate_command_success(taskapi_spec_path):
    result = CliRunner().invoke(cli, ["validate", "--spec", taskapi_spec_path])
    assert result.exit_code == 0
    assert "Task API" in result.output
    assert "Endpoints discovered: 6" in result.output


def test_validate_command_missing_file():
    result = CliRunner().invoke(cli, ["validate", "--spec", "does-not-exist.yaml"])
    assert result.exit_code == 1
    assert "Invalid OpenAPI document" in result.output


def test_validate_command_malformed_spec(tmp_path):
    bad_spec = tmp_path / "bad.yaml"
    bad_spec.write_text("openapi: 2.0\ninfo:\n  title: x\n  version: '1'\npaths: {}\n")
    result = CliRunner().invoke(cli, ["validate", "--spec", str(bad_spec)])
    assert result.exit_code == 1
    assert "Unsupported OpenAPI version" in result.output


def test_run_command_executes_against_demo_app(tmp_path, taskapi_model):
    import json

    from redline.models.testcase import TestCase

    case = TestCase.model_validate(
        {
            "test_id": "TC-001",
            "endpoint": "/health",
            "method": "GET",
            "category": "positive",
            "name": "health check",
            "purpose": "verify health endpoint responds",
            "request": {},
            "expected_status": 200,
            "assertions": [{"type": "status_code", "expected": 200}],
        }
    )
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(json.dumps([case.model_dump(mode="json")]))

    result = CliRunner().invoke(
        cli,
        [
            "run",
            "--cases", str(cases_path),
            "--demo-app",
            "--output-dir", str(tmp_path / "reports"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "1 passed" in result.output
