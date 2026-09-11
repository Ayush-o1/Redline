"""Executes validated test cases via pytest, in an isolated subprocess.

Generated test cases are pure data (see harness_test.py) so there is no
arbitrary code to sandbox -- but we still run pytest as a subprocess, not
in-process, so a hanging or resource-heavy target API cannot destabilize the
Redline CLI process itself, and so the harness gets a clean plugin/import
state on every run.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from pydantic import BaseModel, Field

from redline.models.testcase import TestCase

_HARNESS_PATH = Path(__file__).parent / "harness_test.py"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_NODEID_ID_RE = re.compile(r"\[(.+)\]$")


class TestOutcome(BaseModel):
    test_id: str
    outcome: str  # "passed" | "failed" | "error" | "skipped"
    message: str | None = None
    duration_seconds: float = 0.0


class ExecutionResult(BaseModel):
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    outcomes: list[TestOutcome] = Field(default_factory=list)
    raw_exit_code: int | None = None
    infrastructure_error: str | None = None


class ExecutionError(Exception):
    """Raised when pytest itself could not be run or its report could not be read."""


def _test_id_from_nodeid(nodeid: str) -> str:
    match = _NODEID_ID_RE.search(nodeid)
    return match.group(1) if match else nodeid


def execute_test_cases(
    cases: list[TestCase],
    *,
    output_dir: Path,
    run_id: str,
    target_base_url: str | None = None,
    use_demo_app: bool = False,
    timeout_seconds: float = 120.0,
) -> ExecutionResult:
    if not cases:
        return ExecutionResult()

    run_dir = output_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    cases_file = run_dir / "cases.json"
    cases_file.write_text(
        json.dumps([c.model_dump(mode="json") for c in cases], indent=2), encoding="utf-8"
    )
    report_file = run_dir / "pytest-report.json"

    env = os.environ.copy()
    env["REDLINE_CASES_FILE"] = str(cases_file)
    if target_base_url:
        env["REDLINE_TARGET_BASE_URL"] = target_base_url
    if use_demo_app:
        env["REDLINE_USE_DEMO_APP"] = "1"
        # The bundled demo app's fixed credential, so `--demo-app` works with zero
        # configuration; a real target should set REDLINE_AUTH_TOKEN explicitly.
        env.setdefault("REDLINE_AUTH_TOKEN", env.get("DEMO_API_KEY", "test-key"))

    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in [str(_REPO_ROOT), str(_REPO_ROOT / "src"), existing_pythonpath] if p
    )

    command = [
        sys.executable,
        "-m",
        "pytest",
        str(_HARNESS_PATH),
        "-p",
        "no:cacheprovider",
        "--json-report",
        f"--json-report-file={report_file}",
        "-q",
    ]

    start = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            env=env,
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExecutionError(f"Test execution timed out after {timeout_seconds}s") from exc
    duration = time.monotonic() - start

    if not report_file.exists():
        raise ExecutionError(
            "pytest did not produce a report file. stdout:\n"
            f"{completed.stdout}\nstderr:\n{completed.stderr}"
        )

    report_data = json.loads(report_file.read_text(encoding="utf-8"))
    summary = report_data.get("summary", {})
    outcomes: list[TestOutcome] = []
    for test in report_data.get("tests", []):
        outcome = test.get("outcome", "unknown")
        message = None
        if outcome != "passed":
            call = test.get("call") or test.get("setup") or {}
            message = call.get("longrepr")
        outcomes.append(
            TestOutcome(
                test_id=_test_id_from_nodeid(test.get("nodeid", "")),
                outcome=outcome,
                message=message,
                duration_seconds=test.get("duration", 0.0),
            )
        )

    return ExecutionResult(
        total=summary.get("total", len(outcomes)),
        passed=summary.get("passed", 0),
        failed=summary.get("failed", 0),
        errors=summary.get("error", 0),
        skipped=summary.get("skipped", 0),
        duration_seconds=duration,
        outcomes=outcomes,
        raw_exit_code=completed.returncode,
    )
