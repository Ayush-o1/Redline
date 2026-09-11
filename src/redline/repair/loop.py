"""The repair loop: give the model one failing test case and just enough context to fix it.

Design constraints (see docs/DECISIONS.md "limited retry loop"):
- Only one failing case + its error message + endpoint context is sent back to the
  model -- never the full environment, other test cases, or prior attempts' raw text.
- A repaired case is only ever accepted after it passes the same deterministic
  validation every generated case goes through. The repair loop cannot bypass
  validation.
- Retries are capped by max_retries; every attempt is recorded, win or lose.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from redline.generation.generator import GenerationError, repair_test_case
from redline.generation.provider import LLMProvider
from redline.models.api import ApiModel, Endpoint
from redline.models.testcase import TestCase
from redline.validation.validator import validate_test_cases


class RepairAttempt(BaseModel):
    test_id: str
    attempt_number: int
    error_before: str
    outcome: str  # "repaired" | "still_invalid" | "generation_failed"


class RepairStepResult(BaseModel):
    candidate: TestCase | None
    accepted: bool
    next_error: str
    record: RepairAttempt


def attempt_repair(
    endpoint: Endpoint,
    failing_case_dict: dict,
    error_message: str,
    attempt_number: int,
    provider: LLMProvider,
    api: ApiModel,
) -> RepairStepResult:
    """Try once to fix a failing case (given as a plain dict so callers can chain
    retries using a previous attempt's output). Returns the candidate (if the
    model's response at least matched the TestCase schema), whether it was
    accepted by deterministic validation, and -- if not -- the error message
    to feed into the next attempt.
    """
    try:
        candidate = repair_test_case(
            endpoint, failing_case_dict, error_message, attempt_number, provider
        )
    except GenerationError as exc:
        return RepairStepResult(
            candidate=None,
            accepted=False,
            next_error=str(exc),
            record=RepairAttempt(
                test_id=failing_case_dict.get("test_id", "unknown"),
                attempt_number=attempt_number,
                error_before=error_message,
                outcome="generation_failed",
            ),
        )

    result = validate_test_cases([candidate], api)
    if result.valid:
        return RepairStepResult(
            candidate=result.valid[0],
            accepted=True,
            next_error="",
            record=RepairAttempt(
                test_id=candidate.test_id,
                attempt_number=attempt_number,
                error_before=error_message,
                outcome="repaired",
            ),
        )

    next_error = (
        result.rejected[0].message if result.rejected else "failed deterministic validation"
    )
    return RepairStepResult(
        candidate=candidate,
        accepted=False,
        next_error=next_error,
        record=RepairAttempt(
            test_id=candidate.test_id,
            attempt_number=attempt_number,
            error_before=error_message,
            outcome="still_invalid",
        ),
    )


def repair_until_valid_or_exhausted(
    endpoint: Endpoint,
    failing_case: TestCase,
    error_message: str,
    max_retries: int,
    provider: LLMProvider,
    api: ApiModel,
    verify: Callable[[TestCase], tuple[bool, str | None]] | None = None,
) -> tuple[TestCase | None, list[RepairAttempt]]:
    """Run up to max_retries repair rounds, chaining each attempt's output into
    the next round's input. Returns the first accepted TestCase, or None if the
    retry budget was exhausted, plus the full history of attempts.

    This one function backs both places Redline repairs a case:
    - a case rejected by deterministic validation: `verify` is left as None, so
      passing validation is enough to count as fixed.
    - a case that passed validation but failed execution: the caller passes a
      `verify` callback that re-runs the case against the target and reports
      pass/fail, so a structurally-valid-but-still-failing fix keeps retrying
      instead of being accepted early.
    """
    records: list[RepairAttempt] = []
    current_dict = failing_case.model_dump(mode="json")
    current_error = error_message

    for attempt_number in range(1, max_retries + 1):
        step = attempt_repair(endpoint, current_dict, current_error, attempt_number, provider, api)
        records.append(step.record)

        if not step.accepted or step.candidate is None:
            if step.candidate is not None:
                current_dict = step.candidate.model_dump(mode="json")
            current_error = step.next_error or current_error
            continue

        if verify is None:
            return step.candidate, records

        passed, failure_message = verify(step.candidate)
        if passed:
            return step.candidate, records
        current_dict = step.candidate.model_dump(mode="json")
        current_error = failure_message or "test still fails after repair"

    return None, records
