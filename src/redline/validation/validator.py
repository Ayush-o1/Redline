"""Deterministic validation of generated test cases against the normalized API model.

This is deliberately independent of the LLM: every check here is a plain
Python comparison against redline.models.api.ApiModel, so validation results
are 100% reproducible given the same generated test case and the same spec.
A test case must pass every check here before it is eligible for execution.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from redline.models.api import ApiModel, Endpoint
from redline.models.testcase import AssertionType, TestCase

# Common HTTP error codes APIs frequently return without documenting them
# explicitly in the OpenAPI spec (e.g. a global auth middleware returning 401).
_COMMON_UNDOCUMENTED_STATUSES = {400, 401, 403, 404, 405, 409, 415, 422, 429, 500}

_CONTROL_CHAR_RE = re.compile(r"[\r\n\x00]")


class ValidationIssue(BaseModel):
    test_id: str
    message: str


class ValidationResult(BaseModel):
    valid: list[TestCase]
    rejected: list[ValidationIssue]
    duplicates_removed: int = 0

    @property
    def total_considered(self) -> int:
        return len(self.valid) + len(self.rejected) + self.duplicates_removed


def _documented_statuses(endpoint: Endpoint) -> set[int]:
    statuses = set()
    for r in endpoint.responses:
        try:
            statuses.add(int(r.status_code))
        except ValueError:
            continue  # e.g. "default" or "2XX" patterns are not enumerable
    return statuses


def _has_control_chars(value: object) -> bool:
    return isinstance(value, str) and bool(_CONTROL_CHAR_RE.search(value))


def _check_injection_safety(case: TestCase) -> str | None:
    for name, value in {**case.request.headers, **case.request.path_params}.items():
        if _has_control_chars(value):
            return f"parameter/header '{name}' contains control characters (possible injection)"
    return None


def _check_endpoint_exists(case: TestCase, api: ApiModel) -> tuple[Endpoint | None, str | None]:
    endpoint = api.find_endpoint(case.endpoint, case.method)
    if endpoint is None:
        return None, f"no endpoint '{case.method} {case.endpoint}' exists in the API spec"
    return endpoint, None


def _check_path_params(case: TestCase, endpoint: Endpoint) -> str | None:
    for p in endpoint.path_parameters:
        if p.name not in case.request.path_params:
            return f"missing required path parameter '{p.name}' needed to construct the URL"
    return None


def _check_known_parameters(case: TestCase, endpoint: Endpoint) -> str | None:
    known_query = {p.name for p in endpoint.parameters if p.location.value == "query"}
    known_header = {p.name for p in endpoint.parameters if p.location.value == "header"}
    known_header |= {s.header_name for s in endpoint.security if s.header_name}
    known_path = {p.name for p in endpoint.path_parameters}

    for name in case.request.query_params:
        if name not in known_query:
            return f"references unknown query parameter '{name}'"
    for name in case.request.path_params:
        if name not in known_path:
            return f"references unknown path parameter '{name}'"
    for name in case.request.headers:
        if name.lower() in ("content-type", "authorization", "accept"):
            continue  # always allowed: standard transport-level headers
        if name not in known_header:
            return f"references unknown header parameter '{name}'"
    return None


def _check_body_fields(case: TestCase, endpoint: Endpoint) -> str | None:
    if case.request.body is None:
        return None
    if endpoint.request_body is None:
        return "sends a request body but the endpoint defines no request body"
    if not isinstance(case.request.body, dict):
        return None  # list/array bodies are not field-checked
    known_fields = {f.name for f in endpoint.request_body.fields}
    if not known_fields:
        return None  # schema had no declared properties; nothing to check against
    for name in case.request.body:
        if name not in known_fields:
            return f"request body references unknown field '{name}'"
    return None


def _check_expected_status(case: TestCase, endpoint: Endpoint) -> str | None:
    documented = _documented_statuses(endpoint)
    allowed = documented | _COMMON_UNDOCUMENTED_STATUSES
    if documented and case.expected_status not in allowed:
        return (
            f"expected_status {case.expected_status} is neither documented for this "
            f"endpoint ({sorted(documented)}) nor a common error status"
        )
    return None


def _check_status_assertion_consistency(case: TestCase) -> str | None:
    for a in case.assertions:
        if a.type == AssertionType.STATUS_CODE and a.expected != case.expected_status:
            return (
                f"status_code assertion expects {a.expected} but "
                f"expected_status is {case.expected_status}"
            )
    return None


_CHECKS = [
    _check_path_params,
    _check_known_parameters,
    _check_body_fields,
    _check_expected_status,
]


def validate_test_cases(cases: list[TestCase], api: ApiModel) -> ValidationResult:
    seen_keys: set[tuple] = set()
    duplicates_removed = 0
    deduped: list[TestCase] = []
    for case in cases:
        key = case.dedup_key()
        if key in seen_keys:
            duplicates_removed += 1
            continue
        seen_keys.add(key)
        deduped.append(case)

    valid: list[TestCase] = []
    rejected: list[ValidationIssue] = []

    for case in deduped:
        endpoint, error = _check_endpoint_exists(case, api)
        if error or endpoint is None:
            rejected.append(ValidationIssue(test_id=case.test_id, message=error or "unknown error"))
            continue

        injection_error = _check_injection_safety(case)
        if injection_error:
            rejected.append(ValidationIssue(test_id=case.test_id, message=injection_error))
            continue

        assertion_error = _check_status_assertion_consistency(case)
        if assertion_error:
            rejected.append(ValidationIssue(test_id=case.test_id, message=assertion_error))
            continue

        failure = None
        for check in _CHECKS:
            failure = check(case, endpoint)
            if failure:
                break
        if failure:
            rejected.append(ValidationIssue(test_id=case.test_id, message=failure))
            continue

        valid.append(case)

    return ValidationResult(valid=valid, rejected=rejected, duplicates_removed=duplicates_removed)
