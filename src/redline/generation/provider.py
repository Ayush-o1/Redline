"""LLM provider abstraction.

A Protocol plus two implementations: OpenAIProvider (real, used interactively
and in the optional integration workflow) and MockProvider (deterministic,
used in unit/integration tests and CI so the test suite never depends on a
paid API -- see docs/DECISIONS.md).
"""

from __future__ import annotations

from typing import Protocol


class LLMProvider(Protocol):
    def complete(self, *, system: str, user: str) -> str:
        """Return the raw text completion for a given system/user prompt pair."""
        ...


class ProviderError(Exception):
    """Raised when the underlying LLM API call fails."""


class OpenAIProvider:
    def __init__(self, api_key: str, model: str = "gpt-4o-mini", timeout: float = 30.0) -> None:
        if not api_key:
            raise ProviderError(
                "OPENAI_API_KEY is not set. Export it or add it to a .env file "
                "(see .env.example)."
            )
        # Imported lazily so the `openai` package is only required when this
        # provider is actually used (mocked tests never import it).
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, timeout=timeout)
        self._model = model

    def complete(self, *, system: str, user: str) -> str:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0.2,
            )
        except Exception as exc:  # openai raises various subclasses of Exception
            raise ProviderError(f"OpenAI request failed: {exc}") from exc

        content = response.choices[0].message.content
        if not content:
            raise ProviderError("OpenAI returned an empty response")
        return content


class MockProvider:
    """Deterministic provider for tests: returns queued canned responses in order.

    Each call to complete() pops the next response from the queue. If the
    queue is exhausted, the last response is repeated (useful when a test
    only cares about the first N calls and lets a repair loop run out).
    """

    def __init__(self, responses: list[str]) -> None:
        if not responses:
            raise ValueError("MockProvider requires at least one response")
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[index]


def build_provider(settings) -> LLMProvider:  # settings: redline.config.Settings
    if settings.llm_provider == "openai":
        return OpenAIProvider(
            api_key=settings.openai_api_key or "",
            model=settings.model,
            timeout=settings.request_timeout,
        )
    raise ProviderError(f"Unknown LLM provider: {settings.llm_provider}")
