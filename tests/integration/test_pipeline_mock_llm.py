"""End-to-end pipeline tests: real OpenAPI parsing, real deterministic validation,
real pytest execution against the in-process demo app -- only the LLM is mocked.

This is the test that proves the full story (spec -> plan -> generate -> validate
-> execute -> repair -> report) actually works together, not just in isolation.
"""

from __future__ import annotations

from pathlib import Path

from redline.config.settings import Settings
from redline.core.pipeline import run_pipeline
from redline.generation.provider import MockProvider
from tests.conftest import load_mock_response

SINGLE_ENDPOINT_SPEC = str(Path(__file__).parent.parent / "fixtures" / "single_endpoint_api.yaml")


def _settings(tmp_path: Path, max_retries: int = 2) -> Settings:
    return Settings(
        llm_provider="mock",
        model="mock",
        max_retries=max_retries,
        output_dir=str(tmp_path / "reports"),
    )


def test_full_pipeline_all_cases_pass(tmp_path: Path):
    provider = MockProvider(
        [
            load_mock_response("plan_get_task"),
            load_mock_response("testcases_get_task_valid"),
        ]
    )
    report = run_pipeline(
        SINGLE_ENDPOINT_SPEC, _settings(tmp_path), provider,
        output_dir=tmp_path / "reports", use_demo_app=True,
    )

    assert report.final_status == "PASSED"
    assert report.total_generated == 3
    assert report.total_validated == 3
    assert report.total_rejected == 0
    assert report.total_executed == 3
    assert report.total_passed == 3
    assert report.total_failed == 0

    json_report = tmp_path / "reports" / f"run-{report.run_id}.json"
    assert json_report.exists()
    plan_md = tmp_path / "reports" / "test-plan.md"
    assert plan_md.exists()
    assert "TC-001" in plan_md.read_text()


def test_full_pipeline_repairs_invalid_case_then_passes(tmp_path: Path):
    provider = MockProvider(
        [
            load_mock_response("plan_get_task"),
            load_mock_response("testcases_with_one_invalid"),
            load_mock_response("repaired_tc002"),
        ]
    )
    report = run_pipeline(
        SINGLE_ENDPOINT_SPEC, _settings(tmp_path), provider,
        output_dir=tmp_path / "reports", use_demo_app=True,
    )

    assert report.total_generated == 2
    assert report.total_rejected == 0  # the invalid one got repaired
    assert report.total_validated == 2
    assert report.total_passed == 2
    assert report.final_status == "PASSED"
    assert len(report.repair_attempts) == 1
    assert report.repair_attempts[0].outcome == "repaired"


def test_full_pipeline_repairs_execution_failure_then_passes(tmp_path: Path):
    # TC-001 is structurally valid (404 is a documented status for this endpoint,
    # so it passes validation) but wrong: task_id=1 exists in the demo app, so the
    # real response is 200, not 404. This exercises the *other* repair path --
    # a case that fails at execution time, not validation time.
    provider = MockProvider(
        [
            load_mock_response("plan_get_task"),
            load_mock_response("testcase_wrong_status"),
            load_mock_response("repaired_correct_status"),
        ]
    )
    report = run_pipeline(
        SINGLE_ENDPOINT_SPEC, _settings(tmp_path), provider,
        output_dir=tmp_path / "reports", use_demo_app=True,
    )

    assert report.total_generated == 1
    assert report.total_validated == 1  # nothing rejected by validation
    assert report.total_executed == 1
    assert report.total_passed == 1
    assert report.total_failed == 0
    assert report.final_status == "PASSED"
    assert len(report.repair_attempts) == 1
    assert report.repair_attempts[0].outcome == "repaired"


def test_full_pipeline_gives_up_after_max_retries(tmp_path: Path):
    # The mock always returns the same broken case, no matter how many times we ask.
    provider = MockProvider(
        [
            load_mock_response("plan_get_task"),
            load_mock_response("testcases_with_one_invalid"),
            load_mock_response("repair_needed"),
        ]
    )
    report = run_pipeline(
        SINGLE_ENDPOINT_SPEC, _settings(tmp_path, max_retries=2), provider,
        output_dir=tmp_path / "reports", use_demo_app=True,
    )

    assert report.total_generated == 2
    assert report.total_rejected == 1  # never got fixed
    assert report.total_validated == 1
    assert len(report.repair_attempts) == 2  # exactly max_retries attempts, no more
    assert all(a.outcome == "still_invalid" for a in report.repair_attempts)


def test_full_pipeline_no_scenarios_yields_no_tests(tmp_path: Path):
    provider = MockProvider(
        ['{"endpoint": "/tasks/{task_id}", "method": "GET", "scenarios": [], "notes": ""}']
    )
    report = run_pipeline(
        SINGLE_ENDPOINT_SPEC, _settings(tmp_path), provider,
        output_dir=tmp_path / "reports", use_demo_app=True,
    )
    assert report.final_status == "NO_TESTS"
    assert report.total_generated == 0


def test_full_pipeline_generation_failure_is_recorded_not_fatal(tmp_path: Path):
    provider = MockProvider([load_mock_response("malformed")])
    report = run_pipeline(
        SINGLE_ENDPOINT_SPEC, _settings(tmp_path), provider,
        output_dir=tmp_path / "reports", use_demo_app=True,
    )
    assert report.final_status == "NO_TESTS"
    assert report.endpoints[0].plan_scenarios == 0
