from __future__ import annotations

from redline.models.api import HttpMethod, ParameterLocation


def test_endpoint_count(taskapi_model):
    assert len(taskapi_model.endpoints) == 6


def test_health_endpoint_has_no_auth(taskapi_model):
    ep = taskapi_model.find_endpoint("/health", "GET")
    assert ep is not None
    assert ep.requires_auth is False


def test_get_task_requires_auth_with_header_name(get_task_endpoint):
    assert get_task_endpoint.requires_auth is True
    assert get_task_endpoint.security[0].header_name == "X-API-Key"


def test_get_task_path_parameter(get_task_endpoint):
    path_params = get_task_endpoint.path_parameters
    assert len(path_params) == 1
    assert path_params[0].name == "task_id"
    assert path_params[0].required is True
    assert path_params[0].location == ParameterLocation.PATH
    assert path_params[0].example == 1


def test_list_tasks_query_parameter_enum(taskapi_model):
    ep = taskapi_model.find_endpoint("/tasks", "GET")
    query_params = [p for p in ep.parameters if p.location == ParameterLocation.QUERY]
    assert len(query_params) == 1
    assert query_params[0].name == "status"
    assert query_params[0].required is False
    assert set(query_params[0].enum_values) == {"open", "done"}


def test_create_task_request_body_fields(taskapi_model):
    ep = taskapi_model.find_endpoint("/tasks", "POST")
    assert ep.method == HttpMethod.POST
    assert ep.request_body is not None
    fields_by_name = {f.name: f for f in ep.request_body.fields}
    assert fields_by_name["title"].required is True
    assert fields_by_name["status"].required is False
    assert set(fields_by_name["status"].enum_values) == {"open", "done"}


def test_responses_extracted(taskapi_model):
    ep = taskapi_model.find_endpoint("/tasks/{task_id}", "GET")
    status_codes = {r.status_code for r in ep.responses}
    assert status_codes == {"200", "404", "401"}


def test_find_endpoint_missing_returns_none(taskapi_model):
    assert taskapi_model.find_endpoint("/nope", "GET") is None
