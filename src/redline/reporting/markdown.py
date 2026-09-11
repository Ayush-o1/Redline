"""Human-reviewable rendering of the generated test cases.

The point of this file is explicitly NOT automation: it exists so a human
reviewer can see exactly what the model proposed and what Redline accepted or
rejected, without reading JSON. This is what keeps the project's framing
honest -- AI accelerates test design, a human (or the validator, acting on
the human's behalf) still gets the final say.
"""

from __future__ import annotations

from pathlib import Path

from redline.models.testcase import TestCase
from redline.validation.validator import ValidationIssue


def render_test_plan_markdown(
    spec_title: str,
    spec_version: str,
    valid_cases_by_endpoint: dict[tuple[str, str], list[TestCase]],
    rejected_by_endpoint: dict[tuple[str, str], list[ValidationIssue]],
) -> str:
    lines = [f"# Test Plan: {spec_title} v{spec_version}", ""]

    endpoints = sorted(set(valid_cases_by_endpoint) | set(rejected_by_endpoint))
    for method, path in endpoints:
        lines.append(f"## {method} {path}")
        lines.append("")

        cases = valid_cases_by_endpoint.get((method, path), [])
        if cases:
            lines.append("| Test ID | Category | Name | Expected Status | Purpose |")
            lines.append("|---|---|---|---|---|")
            for c in cases:
                lines.append(
                    f"| {c.test_id} | {c.category.value} | {c.name} | "
                    f"{c.expected_status} | {c.purpose} |"
                )
            lines.append("")
        else:
            lines.append("_No test cases passed validation for this endpoint._")
            lines.append("")

        rejected = rejected_by_endpoint.get((method, path), [])
        if rejected:
            lines.append("**Rejected during validation:**")
            lines.append("")
            for issue in rejected:
                lines.append(f"- `{issue.test_id}`: {issue.message}")
            lines.append("")

    return "\n".join(lines)


def write_test_plan_markdown(content: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "test-plan.md"
    path.write_text(content, encoding="utf-8")
    return path
