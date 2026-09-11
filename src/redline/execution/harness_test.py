"""The trusted, fixed pytest harness that executes generated test cases.

This file is part of Redline's own source code -- it is never generated or
modified by the LLM. The model only ever produces *data* (validated TestCase
JSON); this harness is the only thing that turns that data into HTTP requests
and assertions. That split is what makes LLM output safe to act on: there is
no code path where model output is interpreted as Python (see
docs/ARCHITECTURE.md, "Why LLM output is treated as untrusted").
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
import pytest


def _load_cases() -> list[dict]:
    path = os.environ["REDLINE_CASES_FILE"]
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _uses_demo_app() -> bool:
    return os.environ.get("REDLINE_USE_DEMO_APP") == "1"


def _base_url() -> str:
    return os.environ.get("REDLINE_TARGET_BASE_URL") or "http://testserver"


async def _send_via_demo_app(method: str, path: str, **kwargs: Any) -> httpx.Response:
    """ASGITransport only implements the async transport interface, so the
    in-process demo app is always called through an AsyncClient, even though
    the pytest test function itself stays a plain sync function (see
    test_generated_case below).
    """
    from demo_app.app import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=_base_url(), timeout=10.0) as client:
        return await client.request(method, path, **kwargs)


def _send_request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    if _uses_demo_app():
        return asyncio.run(_send_via_demo_app(method, path, **kwargs))
    with httpx.Client(base_url=_base_url(), timeout=10.0) as client:
        return client.request(method, path, **kwargs)


_AUTH_PLACEHOLDER = "AUTH_TOKEN_PLACEHOLDER"


def _resolve_headers(case: dict) -> dict:
    """Substitute the auth placeholder with the real credential, kept out of the
    prompt sent to the model (see redline.generation.prompts).
    """
    headers = dict(case["request"]["headers"])
    token = os.environ.get("REDLINE_AUTH_TOKEN")
    if token:
        for name, value in headers.items():
            if value == _AUTH_PLACEHOLDER:
                headers[name] = token
    return headers


def _resolve_path(case: dict) -> str:
    path = case["endpoint"]
    for name, value in case["request"]["path_params"].items():
        path = path.replace("{" + name + "}", str(value))
    return path


def _get_field(data: Any, field_path: str) -> Any:
    current = data
    for part in field_path.split("."):
        if isinstance(current, list):
            current = current[int(part)]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(field_path)
    return current


_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "str": (str,),
    "int": (int,),
    "float": (int, float),
    "bool": (bool,),
    "list": (list,),
    "dict": (dict,),
}


def _run_assertions(case: dict, response: httpx.Response) -> list[str]:
    errors: list[str] = []
    try:
        body = response.json()
    except ValueError:
        body = None

    for assertion in case["assertions"]:
        kind = assertion["type"]
        field = assertion.get("field")
        expected = assertion.get("expected")

        if kind == "status_code":
            if response.status_code != expected:
                errors.append(f"expected status {expected}, got {response.status_code}")
            continue

        if kind == "response_not_empty":
            if not body:
                errors.append("response body is empty")
            continue

        if kind == "header_exists":
            if field not in response.headers:
                errors.append(f"header '{field}' not present in response")
            continue

        # Remaining assertion kinds all need to look up a field in the body first.
        try:
            actual = _get_field(body, field)
        except (KeyError, IndexError, TypeError):
            errors.append(f"field '{field}' does not exist in response body")
            continue

        if kind == "field_exists":
            continue  # presence already confirmed by the lookup above
        if kind == "field_equals" and actual != expected:
            errors.append(f"field '{field}' expected {expected!r}, got {actual!r}")
        elif kind == "field_type":
            expected_types = _TYPE_MAP.get(str(expected))
            if expected_types and not isinstance(actual, expected_types):
                errors.append(
                    f"field '{field}' expected type {expected}, got {type(actual).__name__}"
                )

    return errors


CASES = _load_cases()


@pytest.mark.parametrize("case", CASES, ids=[c["test_id"] for c in CASES])
def test_generated_case(case: dict) -> None:
    response = _send_request(
        case["method"],
        _resolve_path(case),
        params=case["request"]["query_params"] or None,
        headers=_resolve_headers(case) or None,
        json=case["request"]["body"],
    )

    errors = _run_assertions(case, response)
    assert not errors, "; ".join(errors)
