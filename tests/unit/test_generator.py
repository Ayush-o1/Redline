from __future__ import annotations

import pytest

from redline.generation.generator import (
    GenerationError,
    generate_test_cases,
    generate_test_plan,
    repair_test_case,
)
from redline.generation.provider import MockProvider
from redline.models.testcase import TestPlan
from tests.conftest import load_mock_response


def test_generate_test_plan_parses_valid_response(get_task_endpoint):
    provider = MockProvider([load_mock_response("plan_get_task")])
    plan = generate_test_plan(get_task_endpoint, provider)
    assert plan.method == "GET"
    assert len(plan.scenarios) == 3


def test_generate_test_plan_rejects_malformed_json(get_task_endpoint):
    provider = MockProvider([load_mock_response("malformed")])
    with pytest.raises(GenerationError, match="not valid JSON"):
        generate_test_plan(get_task_endpoint, provider)


def test_generate_test_cases_parses_valid_response(get_task_endpoint):
    plan = TestPlan(endpoint=get_task_endpoint.path, method="GET", scenarios=["a", "b", "c"])
    provider = MockProvider([load_mock_response("testcases_get_task_valid")])
    cases, attempt = generate_test_cases(get_task_endpoint, plan, provider)
    assert len(cases) == 3
    assert attempt.success is True
    assert attempt.test_case_count == 3


def test_generate_test_cases_rejects_missing_test_cases_key(get_task_endpoint):
    plan = TestPlan(endpoint=get_task_endpoint.path, method="GET", scenarios=["a"])
    provider = MockProvider(['{"not_test_cases": []}'])
    with pytest.raises(GenerationError, match="test_cases"):
        generate_test_cases(get_task_endpoint, plan, provider)


def test_generate_test_cases_partial_schema_failure_raises_when_all_invalid(get_task_endpoint):
    plan = TestPlan(endpoint=get_task_endpoint.path, method="GET", scenarios=["a"])
    provider = MockProvider([load_mock_response("partial")])
    with pytest.raises(GenerationError, match="failed schema validation"):
        generate_test_cases(get_task_endpoint, plan, provider)


def test_generate_test_cases_keeps_valid_cases_when_some_are_invalid(get_task_endpoint):
    plan = TestPlan(endpoint=get_task_endpoint.path, method="GET", scenarios=["a", "b"])
    provider = MockProvider([load_mock_response("testcases_mixed_valid_invalid")])
    cases, attempt = generate_test_cases(get_task_endpoint, plan, provider)
    assert len(cases) == 3  # the 3 valid ones; the malformed 4th is dropped
    assert attempt.success is True


def test_repair_test_case_returns_valid_testcase(get_task_endpoint):
    provider = MockProvider([load_mock_response("repaired")])
    repaired = repair_test_case(
        get_task_endpoint,
        {"test_id": "TC-001"},
        "missing required path parameter 'task_id'",
        1,
        provider,
    )
    assert repaired.request.path_params == {"task_id": "1"}
