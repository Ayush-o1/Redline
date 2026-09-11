from __future__ import annotations

import pytest
from pydantic import ValidationError

from redline.models.testcase import Assertion, RequestSpec, TestCase


def _base_case(**overrides) -> dict:
    data = {
        "test_id": "TC-001",
        "endpoint": "/tasks/{task_id}",
        "method": "get",
        "category": "positive",
        "name": "get task",
        "purpose": "verify task retrieval",
        "request": {"path_params": {"task_id": "1"}},
        "expected_status": 200,
        "assertions": [{"type": "status_code", "expected": 200}],
    }
    data.update(overrides)
    return data


def test_method_is_uppercased():
    case = TestCase.model_validate(_base_case())
    assert case.method == "GET"


def test_invalid_status_code_rejected():
    with pytest.raises(ValidationError):
        TestCase.model_validate(_base_case(expected_status=9999))


def test_field_assertion_without_field_rejected():
    with pytest.raises(ValidationError):
        Assertion.model_validate({"type": "field_equals"})


def test_status_code_assertion_without_field_is_fine():
    assertion = Assertion.model_validate({"type": "status_code", "expected": 200})
    assert assertion.field is None


def test_dedup_key_is_order_independent_for_assertions():
    a1 = [
        {"type": "status_code", "expected": 200},
        {"type": "field_exists", "field": "id"},
    ]
    a2 = list(reversed(a1))
    case1 = TestCase.model_validate(_base_case(assertions=a1))
    case2 = TestCase.model_validate(_base_case(test_id="TC-002", assertions=a2))
    assert case1.dedup_key() == case2.dedup_key()


def test_dedup_key_differs_by_category():
    case1 = TestCase.model_validate(_base_case(category="positive"))
    case2 = TestCase.model_validate(_base_case(test_id="TC-002", category="not_found"))
    assert case1.dedup_key() != case2.dedup_key()


def test_missing_required_field_rejected():
    with pytest.raises(ValidationError):
        TestCase.model_validate({"test_id": "TC-001", "endpoint": "/x", "method": "GET"})


def test_request_spec_defaults():
    spec = RequestSpec()
    assert spec.path_params == {}
    assert spec.query_params == {}
    assert spec.headers == {}
    assert spec.body is None
