"""Logging setup that always passes records through the secret redactor."""

from __future__ import annotations

import logging
import os

from .security import redact


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return redact(original)


def configure_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler()
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # Quiet down chatty libs.
    for noisy in ("httpx", "httpcore", "openai", "playwright"):
        logging.getLogger(noisy).setLevel(max(logging.INFO, root.level))
