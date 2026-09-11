from __future__ import annotations

from pathlib import Path

from redline.execution.runner import execute_test_cases
from redline.models.testcase import TestCase


def _case(**overrides) -> TestCase:
    data = {
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
    data.update(overrides)
    return TestCase.model_validate(data)


def test_execute_empty_list_returns_zeroed_result(tmp_path: Path):
    result = execute_test_cases([], output_dir=tmp_path, run_id="r1")
    assert result.total == 0


def test_execute_passing_case_against_demo_app(tmp_path: Path):
    result = execute_test_cases([_case()], output_dir=tmp_path, run_id="r2", use_demo_app=True)
    assert result.total == 1
    assert result.passed == 1
    assert result.failed == 0
    assert result.outcomes[0].test_id == "TC-001"
    assert result.outcomes[0].outcome == "passed"


def test_execute_failing_case_against_demo_app(tmp_path: Path):
    case = _case(expected_status=418, assertions=[{"type": "status_code", "expected": 418}])
    result = execute_test_cases([case], output_dir=tmp_path, run_id="r3", use_demo_app=True)
    assert result.failed == 1
    assert result.outcomes[0].outcome == "failed"
    assert "418" in (result.outcomes[0].message or "")


def test_execute_field_equals_and_auth_placeholder(tmp_path: Path):
    case = _case(
        endpoint="/tasks/{task_id}",
        request={
            "path_params": {"task_id": "1"},
            "headers": {"X-API-Key": "AUTH_TOKEN_PLACEHOLDER"},
        },
        assertions=[
            {"type": "status_code", "expected": 200},
            {"type": "field_equals", "field": "title", "expected": "Sample task"},
            {"type": "field_type", "field": "id", "expected": "int"},
            {"type": "response_not_empty"},
        ],
    )
    result = execute_test_cases([case], output_dir=tmp_path, run_id="r4", use_demo_app=True)
    assert result.passed == 1, result.outcomes[0].message


def test_execute_auth_failure_without_valid_credentials(tmp_path: Path):
    case = _case(
        endpoint="/tasks/{task_id}",
        request={"path_params": {"task_id": "1"}},
        expected_status=401,
        assertions=[{"type": "status_code", "expected": 401}],
    )
    result = execute_test_cases([case], output_dir=tmp_path, run_id="r5", use_demo_app=True)
    assert result.passed == 1


def test_execute_multiple_cases_are_parametrized(tmp_path: Path):
    cases = [
        _case(test_id="TC-A"),
        _case(
            test_id="TC-B",
            expected_status=418,
            assertions=[{"type": "status_code", "expected": 418}],
        ),
    ]
    result = execute_test_cases(cases, output_dir=tmp_path, run_id="r6", use_demo_app=True)
    assert result.total == 2
    assert result.passed == 1
    assert result.failed == 1
    ids = {o.test_id for o in result.outcomes}
    assert ids == {"TC-A", "TC-B"}
