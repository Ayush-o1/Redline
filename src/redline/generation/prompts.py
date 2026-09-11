"""Prompt construction.

Kept as pure functions returning (system, user) message pairs so prompts can
be unit-tested without touching the network (see tests/unit/test_prompts.py).
Each function builds exactly one concern: system instructions, endpoint
context, or output-schema instructions -- concatenated, never interleaved.
"""

from __future__ import annotations

import json

from redline.models.api import Endpoint
from redline.models.testcase import TestCategory, TestPlan

SYSTEM_INSTRUCTIONS = """You are a senior API test engineer helping design and author \
automated test cases for a REST API described by an OpenAPI specification.

Rules you must follow:
- Only reference parameters, fields, and status codes that actually appear in the \
provided endpoint context. Never invent endpoints, fields, or parameters.
- Prefer a small number of high-value test cases over many redundant ones.
- Only propose scenarios that make sense for this specific endpoint (for example, \
do not propose an authentication scenario for an endpoint with no security requirement).
- Respond with a single JSON object and nothing else: no markdown fences, no prose \
before or after the JSON.
- Never invent a real credential value. When a scenario needs *valid* authentication, \
set the auth header to the exact literal string "AUTH_TOKEN_PLACEHOLDER" -- the test \
harness substitutes the real credential at execution time so it never needs to be sent \
to you. For scenarios that intentionally test missing or invalid authentication, either \
omit the auth header or use an obviously-wrong literal value such as "invalid-token".
"""


def _endpoint_context(endpoint: Endpoint) -> str:
    payload = {
        "operation_id": endpoint.operation_id,
        "method": endpoint.method.value,
        "path": endpoint.path,
        "summary": endpoint.summary,
        "description": endpoint.description,
        "requires_auth": endpoint.requires_auth,
        "auth": [
            {
                "header_name": s.header_name,
                "header_scheme": s.header_scheme,
                "value_for_valid_credentials": "AUTH_TOKEN_PLACEHOLDER",
            }
            for s in endpoint.security
            if s.header_name
        ],
        "parameters": [
            {
                "name": p.name,
                "in": p.location.value,
                "required": p.required,
                "type": p.schema_type,
                "enum": p.enum_values or None,
                "example": p.example,
            }
            for p in endpoint.parameters
        ],
        "request_body": (
            {
                "content_type": endpoint.request_body.content_type,
                "required": endpoint.request_body.required,
                "fields": [
                    {
                        "name": f.name,
                        "type": f.type,
                        "required": f.required,
                        "enum": f.enum_values or None,
                    }
                    for f in endpoint.request_body.fields
                ],
                "example": endpoint.request_body.example,
            }
            if endpoint.request_body
            else None
        ),
        "responses": [
            {"status_code": r.status_code, "description": r.description}
            for r in endpoint.responses
        ],
    }
    return json.dumps(payload, indent=2)


def build_plan_prompt(endpoint: Endpoint) -> tuple[str, str]:
    """Stage 1: ask for a structured test PLAN (scenario descriptions), not code."""
    available_categories = [c.value for c in TestCategory]
    user = f"""Endpoint under test:
{_endpoint_context(endpoint)}

Produce a test plan for this endpoint as JSON matching exactly this shape:
{{
  "endpoint": "{endpoint.path}",
  "method": "{endpoint.method.value}",
  "scenarios": ["<short scenario description>", ...],
  "notes": "<any caveats, or empty string>"
}}

Each scenario description should be a single short sentence describing one test \
case idea (e.g. "missing required field 'name' returns 400"). Only draw scenario \
kinds from this list where they genuinely apply to this endpoint: {available_categories}.
Do not include a scenario category that doesn't apply (e.g. skip "authentication" \
if requires_auth is false, skip "not_found" for endpoints with no path id parameter).
"""
    return SYSTEM_INSTRUCTIONS, user


def build_testcase_prompt(endpoint: Endpoint, plan: TestPlan) -> tuple[str, str]:
    """Stage 2: turn approved scenarios into structured, schema-conformant test cases."""
    schema_instructions = """Respond with JSON matching exactly this shape:
{
  "test_cases": [
    {
      "test_id": "<short unique id, e.g. TC-001>",
      "endpoint": "<the path, exactly as given>",
      "method": "<HTTP method, exactly as given>",
      "category": "<one of: positive, negative, validation, boundary, authentication, \
not_found, schema>",
      "name": "<short human readable name>",
      "purpose": "<one sentence: what this test verifies>",
      "preconditions": ["<setup step>", ...],
      "request": {
        "path_params": {"<name>": "<value>"},
        "query_params": {"<name>": "<value>"},
        "headers": {"<name>": "<value>"},
        "body": {} or null
      },
      "expected_status": <integer HTTP status code>,
      "assertions": [
        {"type": "status_code", "expected": <int>},
        {"type": "field_exists", "field": "<json path>"},
        {"type": "field_equals", "field": "<json path>", "expected": <value>},
        {"type": "field_type", "field": "<json path>", "expected": "<python type name>"},
        {"type": "header_exists", "field": "<header name>"},
        {"type": "response_not_empty"}
      ],
      "tags": ["<tag>", ...]
    }
  ]
}
Only use parameter names, field names, and values consistent with the endpoint context. \
Every test case must include at least one status_code assertion matching expected_status.
"""
    user = f"""Endpoint under test:
{_endpoint_context(endpoint)}

Approved test plan scenarios to implement (one test case per scenario):
{json.dumps(plan.scenarios, indent=2)}

{schema_instructions}"""
    return SYSTEM_INSTRUCTIONS, user


def build_repair_prompt(
    endpoint: Endpoint,
    failing_case: dict,
    error_message: str,
    attempt_number: int,
) -> tuple[str, str]:
    """Repair prompt: only the failing case, the error, and endpoint context -- nothing else."""
    user = f"""Endpoint under test:
{_endpoint_context(endpoint)}

The following generated test case is invalid or failed when executed \
(repair attempt {attempt_number}):

{json.dumps(failing_case, indent=2)}

Problem detected:
{error_message}

Return a corrected version of this single test case as JSON matching exactly this shape \
(the same schema used for test case generation):
{{
  "test_id": "...", "endpoint": "...", "method": "...", "category": "...", "name": "...",
  "purpose": "...", "preconditions": [...], "request": {{"path_params": {{}}, \
"query_params": {{}}, "headers": {{}}, "body": null}}, "expected_status": 0, \
"assertions": [...], "tags": [...]
}}
Only fix what is necessary to resolve the problem described above. Do not change the \
test's intent or category unless that is the source of the problem.
"""
    return SYSTEM_INSTRUCTIONS, user
