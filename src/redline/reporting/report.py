"""Structured run report: the single source of truth for everything printed or written to disk.

Every number surfaced to the user (terminal summary, JSON report, markdown
test-plan) is read from this model -- there is no separate place where
metrics get recomputed or restated, which is what keeps the terminal output,
reports/run-<id>.json, and reports/test-plan.md consistent with each other.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from redline.generation.generator import GenerationAttempt
from redline.repair.loop import RepairAttempt


class EndpointRunResult(BaseModel):
    endpoint: str
    method: str
    plan_scenarios: int = 0
    generated: int = 0
    validated: int = 0
    rejected: int = 0
    duplicates_removed: int = 0
    executed: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    repair_attempts: int = 0
    repaired: int = 0
    rejection_reasons: list[str] = Field(default_factory=list)


class RunReport(BaseModel):
    run_id: str
    spec_title: str
    spec_version: str
    started_at: str
    finished_at: str | None = None
    llm_provider: str
    model: str
    max_retries: int
    target_base_url: str | None = None
    endpoints: list[EndpointRunResult] = Field(default_factory=list)
    generation_attempts: list[GenerationAttempt] = Field(default_factory=list)
    repair_attempts: list[RepairAttempt] = Field(default_factory=list)
    final_status: str = "UNKNOWN"  # PASSED | FAILED | NO_TESTS

    @property
    def total_generated(self) -> int:
        return sum(e.generated for e in self.endpoints)

    @property
    def total_validated(self) -> int:
        return sum(e.validated for e in self.endpoints)

    @property
    def total_rejected(self) -> int:
        return sum(e.rejected for e in self.endpoints)

    @property
    def total_duplicates_removed(self) -> int:
        return sum(e.duplicates_removed for e in self.endpoints)

    @property
    def total_executed(self) -> int:
        return sum(e.executed for e in self.endpoints)

    @property
    def total_passed(self) -> int:
        return sum(e.passed for e in self.endpoints)

    @property
    def total_failed(self) -> int:
        return sum(e.failed for e in self.endpoints)

    @property
    def total_errors(self) -> int:
        return sum(e.errors for e in self.endpoints)

    @property
    def total_repair_attempts(self) -> int:
        return len(self.repair_attempts)

    @property
    def total_repaired(self) -> int:
        return sum(1 for a in self.repair_attempts if a.outcome == "repaired")

    @property
    def endpoints_covered(self) -> int:
        return len({(e.endpoint, e.method) for e in self.endpoints if e.generated > 0})


def write_json_report(report: RunReport, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"run-{report.run_id}.json"
    path.write_text(json.dumps(report.model_dump(mode="json"), indent=2), encoding="utf-8")
    return path


def format_summary(report: RunReport) -> str:
    lines = [
        "Redline Test Run",
        "",
        f"Spec:              {report.spec_title} v{report.spec_version}",
        f"Endpoints covered: {report.endpoints_covered}",
        f"Tests generated:   {report.total_generated}",
        f"Validated:         {report.total_validated}",
        f"Rejected:          {report.total_rejected}",
        f"Duplicates removed:{report.total_duplicates_removed:>3}",
        f"Executed:          {report.total_executed}",
        f"Passed:            {report.total_passed}",
        f"Failed:            {report.total_failed}",
        f"Errors:            {report.total_errors}",
        f"Repair attempts:   {report.total_repair_attempts} "
        f"({report.total_repaired} successful)",
        f"Final status:      {report.final_status}",
    ]
    return "\n".join(lines)
