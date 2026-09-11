"""Orchestrates the full Redline pipeline for one API spec.

    load spec -> normalize -> per endpoint: plan -> generate -> validate ->
    repair invalid -> execute -> repair failures -> aggregate -> report

Kept in one module because the phases share a lot of state (the ApiModel,
the provider, the running report) and splitting it further would just move
that state through extra function arguments without adding clarity.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from redline.config.settings import Settings
from redline.execution.runner import execute_test_cases
from redline.generation.generator import (
    GenerationError,
    generate_test_cases,
    generate_test_plan,
)
from redline.generation.provider import LLMProvider
from redline.logging_utils import get_logger
from redline.models.api import ApiModel, Endpoint
from redline.models.testcase import GenerationAttempt, TestCase
from redline.openapi.loader import load_raw_spec
from redline.openapi.normalize import normalize
from redline.repair.loop import RepairAttempt, attempt_repair
from redline.reporting.markdown import render_test_plan_markdown, write_test_plan_markdown
from redline.reporting.report import EndpointRunResult, RunReport, write_json_report
from redline.validation.validator import ValidationIssue, validate_test_cases

_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _slug(text: str) -> str:
    return _SLUG_RE.sub("-", text).strip("-") or "op"


@dataclass
class EndpointOutcome:
    result: EndpointRunResult
    valid_cases: list[TestCase] = field(default_factory=list)
    rejected_issues: list[ValidationIssue] = field(default_factory=list)
    generation_attempts: list[GenerationAttempt] = field(default_factory=list)
    repair_attempts: list[RepairAttempt] = field(default_factory=list)


def _repair_execution_failures(
    endpoint: Endpoint,
    api: ApiModel,
    provider: LLMProvider,
    max_retries: int,
    failing_cases: dict[str, TestCase],
    failing_messages: dict[str, str],
    *,
    output_dir: Path,
    run_id: str,
    target_base_url: str | None,
    use_demo_app: bool,
) -> tuple[dict[str, TestCase], list[RepairAttempt]]:
    """Repair + re-execute each failing case individually, up to max_retries rounds."""
    records: list[RepairAttempt] = []
    fixed: dict[str, TestCase] = {}
    logger = get_logger()

    for test_id, original_case in failing_cases.items():
        current_case = original_case
        current_error = failing_messages[test_id]

        for attempt_number in range(1, max_retries + 1):
            step = attempt_repair(
                endpoint,
                current_case.model_dump(mode="json"),
                current_error,
                attempt_number,
                provider,
                api,
            )
            records.append(step.record)
            logger.info(
                "repair attempt %s/%s for %s: %s",
                attempt_number,
                max_retries,
                test_id,
                step.record.outcome,
                extra={"phase": "repair"},
            )

            if step.candidate is None:
                current_error = step.next_error or current_error
                continue

            current_case = step.candidate
            if not step.accepted:
                current_error = step.next_error or current_error
                continue

            exec_result = execute_test_cases(
                [current_case],
                output_dir=output_dir,
                run_id=f"{run_id}-repair-{_slug(test_id)}-{attempt_number}",
                target_base_url=target_base_url,
                use_demo_app=use_demo_app,
            )
            outcome = next(
                (o for o in exec_result.outcomes if o.test_id == current_case.test_id), None
            )
            if outcome is not None and outcome.outcome == "passed":
                fixed[test_id] = current_case
                break
            current_error = (outcome.message if outcome and outcome.message else None) or (
                "test still fails after repair"
            )

    return fixed, records


def run_endpoint(
    endpoint: Endpoint,
    api: ApiModel,
    provider: LLMProvider,
    settings: Settings,
    *,
    output_dir: Path,
    run_id: str,
    use_demo_app: bool,
) -> EndpointOutcome:
    logger = get_logger()
    result = EndpointRunResult(endpoint=endpoint.path, method=endpoint.method.value)
    outcome = EndpointOutcome(result=result)

    try:
        plan = generate_test_plan(endpoint, provider)
    except GenerationError as exc:
        logger.warning("test plan generation failed: %s", exc, extra={"phase": "plan"})
        return outcome

    result.plan_scenarios = len(plan.scenarios)
    if not plan.scenarios:
        return outcome

    try:
        cases, gen_attempt = generate_test_cases(endpoint, plan, provider)
    except GenerationError as exc:
        logger.warning("test case generation failed: %s", exc, extra={"phase": "generate"})
        outcome.generation_attempts.append(
            GenerationAttempt(
                attempt_number=1,
                endpoint=endpoint.path,
                method=endpoint.method.value,
                raw_response="",
                success=False,
                error=str(exc),
            )
        )
        return outcome

    outcome.generation_attempts.append(gen_attempt)
    result.generated = len(cases)

    validation_result = validate_test_cases(cases, api)
    result.duplicates_removed = validation_result.duplicates_removed
    valid_cases = list(validation_result.valid)
    still_rejected: list[ValidationIssue] = []

    cases_by_id = {c.test_id: c for c in cases}
    for issue in validation_result.rejected:
        original = cases_by_id.get(issue.test_id)
        if original is None or settings.max_retries <= 0:
            still_rejected.append(issue)
            continue
        current_case = original
        current_error = issue.message
        repaired: TestCase | None = None
        for attempt_number in range(1, settings.max_retries + 1):
            step = attempt_repair(
                endpoint, current_case.model_dump(mode="json"), current_error, attempt_number,
                provider, api,
            )
            outcome.repair_attempts.append(step.record)
            if step.accepted and step.candidate is not None:
                repaired = step.candidate
                break
            if step.candidate is not None:
                current_case = step.candidate
            current_error = step.next_error or current_error
        if repaired is not None:
            valid_cases.append(repaired)
        else:
            still_rejected.append(issue)

    result.rejection_reasons = [i.message for i in still_rejected]
    result.rejected = len(still_rejected)
    result.validated = len(valid_cases)
    outcome.rejected_issues = still_rejected

    if not valid_cases:
        return outcome

    exec_result = execute_test_cases(
        valid_cases,
        output_dir=output_dir,
        run_id=f"{run_id}-{_slug(endpoint.operation_id)}",
        target_base_url=settings.target_base_url,
        use_demo_app=use_demo_app,
    )
    result.executed = exec_result.total
    result.passed = exec_result.passed
    result.failed = exec_result.failed
    result.errors = exec_result.errors
    result.skipped = exec_result.skipped

    failing_kind = {
        o.test_id: o.outcome for o in exec_result.outcomes if o.outcome in ("failed", "error")
    }
    if failing_kind and settings.max_retries > 0:
        failing_messages = {
            o.test_id: (o.message or "test failed") for o in exec_result.outcomes
            if o.test_id in failing_kind
        }
        failing_cases = {c.test_id: c for c in valid_cases if c.test_id in failing_kind}
        fixed, records = _repair_execution_failures(
            endpoint,
            api,
            provider,
            settings.max_retries,
            failing_cases,
            failing_messages,
            output_dir=output_dir,
            run_id=run_id,
            target_base_url=settings.target_base_url,
            use_demo_app=use_demo_app,
        )
        outcome.repair_attempts.extend(records)
        for test_id, kind in failing_kind.items():
            if test_id in fixed:
                result.passed += 1
                if kind == "failed":
                    result.failed -= 1
                else:
                    result.errors -= 1
        for i, case in enumerate(valid_cases):
            if case.test_id in fixed:
                valid_cases[i] = fixed[case.test_id]

    outcome.valid_cases = valid_cases
    return outcome


def run_pipeline(
    spec_path: str,
    settings: Settings,
    provider: LLMProvider,
    *,
    output_dir: Path | None = None,
    use_demo_app: bool = False,
) -> RunReport:
    output_dir = output_dir or Path(settings.output_dir)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    logger = get_logger()

    raw_spec = load_raw_spec(spec_path)
    api = normalize(raw_spec)

    report = RunReport(
        run_id=run_id,
        spec_title=api.title,
        spec_version=api.version,
        started_at=datetime.now(timezone.utc).isoformat(),
        llm_provider=settings.llm_provider,
        model=settings.model,
        max_retries=settings.max_retries,
        target_base_url=settings.target_base_url,
    )

    valid_cases_by_endpoint: dict[tuple[str, str], list[TestCase]] = {}
    rejected_by_endpoint: dict[tuple[str, str], list[ValidationIssue]] = {}

    for endpoint in api.endpoints:
        logger.info(
            "processing endpoint %s %s", endpoint.method.value, endpoint.path,
            extra={"phase": "pipeline"},
        )
        outcome = run_endpoint(
            endpoint, api, provider, settings,
            output_dir=output_dir, run_id=run_id, use_demo_app=use_demo_app,
        )
        report.endpoints.append(outcome.result)
        report.generation_attempts.extend(outcome.generation_attempts)
        report.repair_attempts.extend(outcome.repair_attempts)
        key = (endpoint.method.value, endpoint.path)
        valid_cases_by_endpoint[key] = outcome.valid_cases
        rejected_by_endpoint[key] = outcome.rejected_issues

    report.finished_at = datetime.now(timezone.utc).isoformat()
    if report.total_generated == 0:
        report.final_status = "NO_TESTS"
    elif report.total_failed == 0 and report.total_errors == 0:
        report.final_status = "PASSED"
    else:
        report.final_status = "FAILED"

    write_json_report(report, output_dir)
    markdown = render_test_plan_markdown(
        api.title, api.version, valid_cases_by_endpoint, rejected_by_endpoint
    )
    write_test_plan_markdown(markdown, output_dir)

    return report
