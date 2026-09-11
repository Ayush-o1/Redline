"""Redline's command-line interface.

Each command maps directly onto one phase of the pipeline described in
docs/ARCHITECTURE.md, so the CLI structure mirrors the actual data flow
rather than hiding it behind a single opaque command.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import click

from redline.config.settings import Settings
from redline.core.pipeline import run_pipeline
from redline.execution.runner import execute_test_cases
from redline.generation.generator import GenerationError, generate_test_cases, generate_test_plan
from redline.generation.provider import ProviderError, build_provider
from redline.logging_utils import configure_logging
from redline.models.testcase import TestCase
from redline.openapi.loader import RedlineSpecError, load_raw_spec
from redline.openapi.normalize import normalize
from redline.reporting.markdown import render_test_plan_markdown, write_test_plan_markdown
from redline.reporting.report import format_summary
from redline.validation.validator import ValidationIssue, validate_test_cases


@click.group()
@click.version_option(package_name="redline")
def cli() -> None:
    """Redline: AI-assisted API test planning, generation, validation, and execution."""


def _load_api(spec: str):
    raw = load_raw_spec(spec)
    return normalize(raw)


@cli.command()
@click.option("--spec", required=True, help="Path to an OpenAPI 3.x YAML or JSON file")
def validate(spec: str) -> None:
    """Validate an OpenAPI spec and list the endpoints Redline can target."""
    try:
        api = _load_api(spec)
    except RedlineSpecError as exc:
        click.echo(f"Invalid OpenAPI document:\n{exc}", err=True)
        raise SystemExit(1) from exc

    click.echo(f"Spec OK: {api.title} v{api.version}")
    click.echo(f"Endpoints discovered: {len(api.endpoints)}")
    for ep in api.endpoints:
        auth = " [auth]" if ep.requires_auth else ""
        click.echo(f"  {ep.method.value:<7} {ep.path}{auth}")


@cli.command()
@click.option("--spec", required=True)
def plan(spec: str) -> None:
    """Generate an LLM test plan (scenario list) for every endpoint."""
    configure_logging(uuid.uuid4().hex[:8])
    settings = Settings.from_env()
    try:
        api = _load_api(spec)
        provider = build_provider(settings)
    except (RedlineSpecError, ProviderError) as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc

    for ep in api.endpoints:
        click.echo(f"\n{ep.method.value} {ep.path}")
        try:
            test_plan = generate_test_plan(ep, provider)
        except GenerationError as exc:
            click.echo(f"  generation failed: {exc}", err=True)
            continue
        if not test_plan.scenarios:
            click.echo("  (no scenarios proposed)")
        for scenario in test_plan.scenarios:
            click.echo(f"  - {scenario}")


@cli.command()
@click.option("--spec", required=True)
@click.option("--output-dir", default=None, help="Defaults to $OUTPUT_DIR or ./reports")
def generate(spec: str, output_dir: str | None) -> None:
    """Generate + validate test cases for every endpoint (does not execute them)."""
    configure_logging(uuid.uuid4().hex[:8])
    settings = Settings.from_env()
    out_dir = Path(output_dir or settings.output_dir)

    try:
        api = _load_api(spec)
        provider = build_provider(settings)
    except (RedlineSpecError, ProviderError) as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc

    valid_by_endpoint: dict[tuple[str, str], list[TestCase]] = {}
    rejected_by_endpoint: dict[tuple[str, str], list[ValidationIssue]] = {}
    total_generated = total_valid = total_rejected = 0

    for ep in api.endpoints:
        key = (ep.method.value, ep.path)
        try:
            test_plan = generate_test_plan(ep, provider)
            cases = generate_test_cases(ep, test_plan, provider)[0] if test_plan.scenarios else []
        except GenerationError as exc:
            click.echo(f"{ep.method.value} {ep.path}: generation failed: {exc}", err=True)
            valid_by_endpoint[key] = []
            rejected_by_endpoint[key] = []
            continue

        result = validate_test_cases(cases, api)
        valid_by_endpoint[key] = result.valid
        rejected_by_endpoint[key] = result.rejected
        total_generated += len(cases)
        total_valid += len(result.valid)
        total_rejected += len(result.rejected)

    markdown = render_test_plan_markdown(
        api.title, api.version, valid_by_endpoint, rejected_by_endpoint
    )
    path = write_test_plan_markdown(markdown, out_dir)

    click.echo(
        f"Generated {total_generated} test cases: {total_valid} passed validation, "
        f"{total_rejected} rejected."
    )
    click.echo(f"Test plan written to {path}")


@cli.command(name="run")
@click.option("--cases", "cases_path", required=True, type=click.Path(exists=True))
@click.option("--target-base-url", default=None)
@click.option(
    "--demo-app", is_flag=True, default=False, help="Execute against the in-process demo app"
)
@click.option("--output-dir", default=None)
def run_cmd(
    cases_path: str, target_base_url: str | None, demo_app: bool, output_dir: str | None
) -> None:
    """Execute a previously generated & validated test-case JSON file with pytest."""
    configure_logging(uuid.uuid4().hex[:8])
    settings = Settings.from_env()
    out_dir = Path(output_dir or settings.output_dir)

    raw_cases = json.loads(Path(cases_path).read_text(encoding="utf-8"))
    cases = [TestCase.model_validate(c) for c in raw_cases]

    result = execute_test_cases(
        cases,
        output_dir=out_dir,
        run_id=uuid.uuid4().hex[:8],
        target_base_url=target_base_url or settings.target_base_url,
        use_demo_app=demo_app,
    )

    click.echo(
        f"Executed {result.total} tests: {result.passed} passed, {result.failed} failed, "
        f"{result.errors} errors, {result.skipped} skipped ({result.duration_seconds:.2f}s)"
    )
    for o in result.outcomes:
        if o.outcome != "passed":
            click.echo(f"  {o.outcome.upper():<7} {o.test_id}: {o.message}")

    raise SystemExit(0 if result.failed == 0 and result.errors == 0 else 1)


@cli.command()
@click.option("--spec", required=True)
@click.option("--max-retries", default=None, type=int)
@click.option("--target-base-url", default=None)
@click.option(
    "--demo-app", is_flag=True, default=False, help="Execute against the in-process demo app"
)
@click.option("--output-dir", default=None)
def full(
    spec: str,
    max_retries: int | None,
    target_base_url: str | None,
    demo_app: bool,
    output_dir: str | None,
) -> None:
    """Run the full pipeline end-to-end: plan, generate, validate, execute, repair, report."""
    settings = Settings.from_env()
    if max_retries is not None:
        settings.max_retries = max_retries
    if target_base_url:
        settings.target_base_url = target_base_url
    if output_dir:
        settings.output_dir = output_dir

    configure_logging(uuid.uuid4().hex[:8])

    try:
        provider = build_provider(settings)
    except ProviderError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc

    try:
        report = run_pipeline(
            spec, settings, provider, output_dir=Path(settings.output_dir), use_demo_app=demo_app
        )
    except RedlineSpecError as exc:
        click.echo(f"Invalid OpenAPI document:\n{exc}", err=True)
        raise SystemExit(1) from exc

    click.echo(format_summary(report))
    raise SystemExit(0 if report.final_status == "PASSED" else 1)


if __name__ == "__main__":
    cli()
