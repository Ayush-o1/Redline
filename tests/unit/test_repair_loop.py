from __future__ import annotations

from redline.generation.provider import MockProvider
from redline.models.testcase import TestCase
from redline.repair.loop import attempt_repair, repair_until_valid_or_exhausted
from tests.conftest import load_mock_response


def _broken_case() -> TestCase:
    return TestCase.model_validate(
        {
            "test_id": "TC-001",
            "endpoint": "/tasks/{task_id}",
            "method": "GET",
            "category": "positive",
            "name": "get task",
            "purpose": "verify retrieval",
            "request": {"path_params": {}},  # missing required task_id
            "expected_status": 200,
            "assertions": [{"type": "status_code", "expected": 200}],
        }
    )


def test_attempt_repair_accepts_valid_candidate(get_task_endpoint, taskapi_model):
    provider = MockProvider([load_mock_response("repaired")])
    step = attempt_repair(
        get_task_endpoint,
        _broken_case().model_dump(mode="json"),
        "missing required path parameter 'task_id'",
        1,
        provider,
        taskapi_model,
    )
    assert step.accepted is True
    assert step.record.outcome == "repaired"
    assert step.candidate.request.path_params == {"task_id": "1"}


def test_attempt_repair_rejects_still_invalid_candidate(get_task_endpoint, taskapi_model):
    provider = MockProvider([load_mock_response("repair_needed")])
    step = attempt_repair(
        get_task_endpoint,
        _broken_case().model_dump(mode="json"),
        "missing required path parameter 'task_id'",
        1,
        provider,
        taskapi_model,
    )
    assert step.accepted is False
    assert step.record.outcome == "still_invalid"


def test_attempt_repair_handles_unparseable_model_output(get_task_endpoint, taskapi_model):
    provider = MockProvider([load_mock_response("malformed")])
    step = attempt_repair(
        get_task_endpoint,
        _broken_case().model_dump(mode="json"),
        "some error",
        1,
        provider,
        taskapi_model,
    )
    assert step.candidate is None
    assert step.record.outcome == "generation_failed"


def test_repair_until_valid_or_exhausted_succeeds_on_first_try(get_task_endpoint, taskapi_model):
    provider = MockProvider([load_mock_response("repaired")])
    fixed, records = repair_until_valid_or_exhausted(
        get_task_endpoint, _broken_case(), "missing required path parameter 'task_id'",
        3, provider, taskapi_model,
    )
    assert fixed is not None
    assert len(records) == 1
    assert records[0].outcome == "repaired"


def test_repair_until_valid_or_exhausted_stops_at_max_retries(get_task_endpoint, taskapi_model):
    # Always returns the same still-broken case -- repair should give up after 2 rounds.
    provider = MockProvider([load_mock_response("repair_needed")])
    fixed, records = repair_until_valid_or_exhausted(
        get_task_endpoint, _broken_case(), "missing required path parameter 'task_id'",
        2, provider, taskapi_model,
    )
    assert fixed is None
    assert len(records) == 2
    assert all(r.outcome == "still_invalid" for r in records)
