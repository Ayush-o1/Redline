from __future__ import annotations

import json

from redline.generation.prompts import build_plan_prompt, build_repair_prompt, build_testcase_prompt
from redline.models.testcase import TestPlan


def test_plan_prompt_includes_endpoint_path_and_method(get_task_endpoint):
    system, user = build_plan_prompt(get_task_endpoint)
    assert "JSON" in system
    assert get_task_endpoint.path in user
    assert get_task_endpoint.method.value in user


def test_plan_prompt_never_leaks_real_credentials(get_task_endpoint):
    # The header name is fine to disclose; a real secret value never should be.
    _, user = build_plan_prompt(get_task_endpoint)
    assert "sk-" not in user
    assert "test-key" not in user


def test_testcase_prompt_includes_auth_placeholder_instruction(get_task_endpoint):
    plan = TestPlan(endpoint=get_task_endpoint.path, method="GET", scenarios=["a valid fetch"])
    system, _ = build_testcase_prompt(get_task_endpoint, plan)
    assert "AUTH_TOKEN_PLACEHOLDER" in system


def test_testcase_prompt_includes_scenarios(get_task_endpoint):
    plan = TestPlan(
        endpoint=get_task_endpoint.path,
        method="GET",
        scenarios=["fetching an existing task returns 200"],
    )
    _, user = build_testcase_prompt(get_task_endpoint, plan)
    assert "fetching an existing task returns 200" in user


def test_testcase_prompt_context_is_valid_json_fragment(get_task_endpoint):
    plan = TestPlan(endpoint=get_task_endpoint.path, method="GET", scenarios=["x"])
    _, user = build_testcase_prompt(get_task_endpoint, plan)
    # The endpoint context block itself must be valid JSON so the model sees clean data.
    start = user.index("{")
    end = user.rindex("}", 0, user.index("Approved test plan"))
    json.loads(user[start : end + 1])


def test_repair_prompt_includes_error_and_attempt_number(get_task_endpoint):
    failing = {"test_id": "TC-001", "expected_status": 200}
    _, user = build_repair_prompt(get_task_endpoint, failing, "missing path parameter", 2)
    assert "missing path parameter" in user
    assert "attempt 2" in user
    assert "TC-001" in user
