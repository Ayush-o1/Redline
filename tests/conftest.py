from __future__ import annotations

from pathlib import Path

import pytest

from redline.openapi.loader import load_raw_spec
from redline.openapi.normalize import normalize

FIXTURES_DIR = Path(__file__).parent / "fixtures"
MOCK_LLM_DIR = FIXTURES_DIR / "mock_llm_responses"
EXAMPLES_DIR = Path(__file__).parent.parent / "examples"


def load_mock_response(name: str) -> str:
    for suffix in (".json", ".txt"):
        path = MOCK_LLM_DIR / f"{name}{suffix}"
        if path.exists():
            return path.read_text(encoding="utf-8")
    raise FileNotFoundError(name)


@pytest.fixture
def taskapi_spec_path() -> str:
    return str(EXAMPLES_DIR / "taskapi.yaml")


@pytest.fixture
def taskapi_model(taskapi_spec_path):
    raw = load_raw_spec(taskapi_spec_path)
    return normalize(raw)


@pytest.fixture
def get_task_endpoint(taskapi_model):
    return taskapi_model.find_endpoint("/tasks/{task_id}", "GET")
