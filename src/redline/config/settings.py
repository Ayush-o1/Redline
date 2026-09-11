"""Centralized configuration, loaded from environment variables with sensible defaults.

Kept as a plain dataclass rather than a settings library: Redline has a small,
fixed set of knobs and this keeps the dependency list short (see docs/DECISIONS.md).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    llm_provider: str = "openai"
    model: str = "gpt-4o-mini"
    max_retries: int = 2
    request_timeout: float = 30.0
    output_dir: str = "reports"
    target_base_url: str | None = None
    openai_api_key: str | None = field(default=None, repr=False)
    auth_token: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
            model=os.environ.get("MODEL", "gpt-4o-mini"),
            max_retries=int(os.environ.get("MAX_RETRIES", "2")),
            request_timeout=float(os.environ.get("REQUEST_TIMEOUT", "30")),
            output_dir=os.environ.get("OUTPUT_DIR", "reports"),
            target_base_url=os.environ.get("TARGET_BASE_URL"),
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
            auth_token=os.environ.get("REDLINE_AUTH_TOKEN"),
        )
