"""Turns LLM completions into validated redline.models objects.

This is the boundary where untrusted model output first meets structured
parsing. Nothing past this module ever sees raw LLM text again -- only
Pydantic model instances or a GenerationError.
"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError

from redline.generation.prompts import (
    build_plan_prompt,
    build_repair_prompt,
    build_testcase_prompt,
)
from redline.generation.provider import LLMProvider, ProviderError
from redline.models.api import Endpoint
from redline.models.testcase import GenerationAttempt, TestCase, TestPlan

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class GenerationError(Exception):
    """Raised when LLM output cannot be parsed into a valid structured object."""


def _extract_json(raw: str) -> dict:
    """Best-effort cleanup of near-JSON LLM output (stray markdown fences), then parse.

    We do NOT attempt to repair broken JSON syntax here -- a genuinely malformed
    response should fail validation and flow into the repair loop, not be
    silently patched into something the model never actually produced.
    """
    cleaned = _FENCE_RE.sub("", raw).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise GenerationError(f"Model response is not valid JSON: {exc}") from exc


def generate_test_plan(endpoint: Endpoint, provider: LLMProvider) -> TestPlan:
    system, user = build_plan_prompt(endpoint)
    try:
        raw = provider.complete(system=system, user=user)
    except ProviderError as exc:
        raise GenerationError(f"Provider call failed while generating test plan: {exc}") from exc

    data = _extract_json(raw)
    try:
        return TestPlan.model_validate(data)
    except ValidationError as exc:
        raise GenerationError(f"Model response did not match TestPlan schema: {exc}") from exc


def generate_test_cases(
    endpoint: Endpoint, plan: TestPlan, provider: LLMProvider
) -> tuple[list[TestCase], GenerationAttempt]:
    system, user = build_testcase_prompt(endpoint, plan)
    try:
        raw = provider.complete(system=system, user=user)
    except ProviderError as exc:
        raise GenerationError(
            f"Provider call failed while generating test cases: {exc}"
        ) from exc

    attempt = GenerationAttempt(
        attempt_number=1,
        endpoint=endpoint.path,
        method=endpoint.method.value,
        raw_response=raw,
        success=False,
    )

    try:
        data = _extract_json(raw)
    except GenerationError as exc:
        attempt.error = str(exc)
        raise GenerationError(str(exc)) from exc

    raw_cases = data.get("test_cases")
    if not isinstance(raw_cases, list):
        error = "Model response is missing a 'test_cases' list"
        attempt.error = error
        raise GenerationError(error)

    cases: list[TestCase] = []
    errors: list[str] = []
    for i, raw_case in enumerate(raw_cases):
        try:
            cases.append(TestCase.model_validate(raw_case))
        except ValidationError as exc:
            errors.append(f"test_cases[{i}]: {exc}")

    if errors and not cases:
        error = "All generated test cases failed schema validation:\n" + "\n".join(errors)
        attempt.error = error
        raise GenerationError(error)

    attempt.success = True
    attempt.test_case_count = len(cases)
    return cases, attempt


def repair_test_case(
    endpoint: Endpoint,
    failing_case: dict,
    error_message: str,
    attempt_number: int,
    provider: LLMProvider,
) -> TestCase:
    system, user = build_repair_prompt(endpoint, failing_case, error_message, attempt_number)
    try:
        raw = provider.complete(system=system, user=user)
    except ProviderError as exc:
        raise GenerationError(f"Provider call failed during repair: {exc}") from exc

    data = _extract_json(raw)
    try:
        return TestCase.model_validate(data)
    except ValidationError as exc:
        raise GenerationError(f"Repaired response did not match TestCase schema: {exc}") from exc
