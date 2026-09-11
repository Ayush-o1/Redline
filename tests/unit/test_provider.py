from __future__ import annotations

import pytest

from redline.config.settings import Settings
from redline.generation.provider import MockProvider, OpenAIProvider, ProviderError, build_provider


def test_mock_provider_returns_responses_in_order():
    provider = MockProvider(["first", "second"])
    assert provider.complete(system="s", user="u1") == "first"
    assert provider.complete(system="s", user="u2") == "second"


def test_mock_provider_repeats_last_response_when_exhausted():
    provider = MockProvider(["only"])
    provider.complete(system="s", user="u1")
    assert provider.complete(system="s", user="u2") == "only"


def test_mock_provider_records_calls():
    provider = MockProvider(["r"])
    provider.complete(system="sys", user="usr")
    assert provider.calls == [("sys", "usr")]


def test_openai_provider_requires_api_key():
    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        OpenAIProvider(api_key="")


def test_build_provider_unknown_provider_raises():
    settings = Settings(llm_provider="not-a-real-provider")
    with pytest.raises(ProviderError, match="Unknown LLM provider"):
        build_provider(settings)
