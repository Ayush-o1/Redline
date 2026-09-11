"""Structured logging setup.

Every log record carries a run_id and phase so a single pipeline run can be
grepped out of interleaved output. API keys and request bodies are never
logged (see docs/ARCHITECTURE.md, "Security considerations").
"""

from __future__ import annotations

import logging
import sys


class RunContextFilter(logging.Filter):
    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = self.run_id
        if not hasattr(record, "phase"):
            record.phase = "-"
        return True


def configure_logging(run_id: str, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("redline")
    logger.setLevel(level)
    logger.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] run=%(run_id)s phase=%(phase)s %(message)s"
    )
    handler.setFormatter(formatter)
    handler.addFilter(RunContextFilter(run_id))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("redline")
