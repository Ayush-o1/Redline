from __future__ import annotations

from redline.models.testcase import TestCase
from redline.validation.validator import validate_test_cases


def _case(**overrides) -> TestCase:
    data = {
        "test_id": "TC-001",
        "endpoint": "/tasks/{task_id}",
        "method": "GET",
        "category": "positive",
        "name": "get task",
        "purpose": "verify task retrieval",
        "request": {
            "path_params": {"task_id": "1"},
            "headers": {"X-API-Key": "AUTH_TOKEN_PLACEHOLDER"},
        },
        "expected_status": 200,
        "assertions": [{"type": "status_code", "expected": 200}],
    }
    data.update(overrides)
    return TestCase.model_validate(data)


def test_valid_case_passes(taskapi_model):
    result = validate_test_cases([_case()], taskapi_model)
    assert len(result.valid) == 1
    assert result.rejected == []


def test_unknown_endpoint_rejected(taskapi_model):
    case = _case(endpoint="/does-not-exist")
    result = validate_test_cases([case], taskapi_model)
    assert result.valid == []
    assert "no endpoint" in result.rejected[0].message


def test_wrong_method_rejected(taskapi_model):
    case = _case(method="PATCH")  # not defined for this path at all
    result = validate_test_cases([case], taskapi_model)
    assert result.valid == []
    assert "no endpoint" in result.rejected[0].message


def test_missing_path_param_rejected(taskapi_model):
    case = _case(request={"path_params": {}, "headers": {"X-API-Key": "AUTH_TOKEN_PLACEHOLDER"}})
    result = validate_test_cases([case], taskapi_model)
    assert result.valid == []
    assert "missing required path parameter" in result.rejected[0].message


def test_unknown_query_parameter_rejected(taskapi_model):
    case = _case(
        request={
            "path_params": {"task_id": "1"},
            "query_params": {"bogus": "x"},
            "headers": {"X-API-Key": "AUTH_TOKEN_PLACEHOLDER"},
        }
    )
    result = validate_test_cases([case], taskapi_model)
    assert "unknown query parameter" in result.rejected[0].message


def test_unknown_body_field_rejected(taskapi_model):
    case = _case(
        endpoint="/tasks",
        method="POST",
        request={"path_params": {}, "body": {"unknown_field": "x"}},
        expected_status=201,
        assertions=[{"type": "status_code", "expected": 201}],
    )
    result = validate_test_cases([case], taskapi_model)
    assert "unknown field" in result.rejected[0].message


def test_body_on_endpoint_without_request_body_rejected(taskapi_model):
    case = _case(request={"path_params": {"task_id": "1"}, "body": {"x": 1}})
    result = validate_test_cases([case], taskapi_model)
    assert "defines no request body" in result.rejected[0].message


def test_undocumented_and_uncommon_status_rejected(taskapi_model):
    case = _case(
        expected_status=418,
        assertions=[{"type": "status_code", "expected": 418}],
    )
    result = validate_test_cases([case], taskapi_model)
    assert "neither documented" in result.rejected[0].message


def test_common_undocumented_error_status_allowed(taskapi_model):
    # 401 is documented for this endpoint, so this is really a documented-status
    # check; 429 is not documented anywhere but is a common status Redline allows.
    case = _case(
        expected_status=429,
        assertions=[{"type": "status_code", "expected": 429}],
    )
    result = validate_test_cases([case], taskapi_model)
    assert result.valid, result.rejected


def test_status_assertion_mismatch_rejected(taskapi_model):
    case = _case(assertions=[{"type": "status_code", "expected": 404}])
    result = validate_test_cases([case], taskapi_model)
    assert "expects 404" in result.rejected[0].message


def test_header_injection_rejected(taskapi_model):
    case = _case(
        request={"path_params": {"task_id": "1"}, "headers": {"X-API-Key": "a\r\nEvil: 1"}}
    )
    result = validate_test_cases([case], taskapi_model)
    assert "control characters" in result.rejected[0].message


def test_duplicate_cases_are_deduplicated(taskapi_model):
    case1 = _case(test_id="TC-001")
    case2 = _case(test_id="TC-002")  # same method/path/category/assertions -> duplicate
    result = validate_test_cases([case1, case2], taskapi_model)
    assert result.duplicates_removed == 1
    assert len(result.valid) == 1


def test_auth_header_from_security_scheme_is_known(taskapi_model):
    case = _case(request={"path_params": {"task_id": "1"}, "headers": {"X-API-Key": "x"}})
    result = validate_test_cases([case], taskapi_model)
    assert result.valid
