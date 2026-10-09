"""Structured JSON logging (structlog) with secret redaction.

Any event key that looks like a secret (password, secret, token, api key) is replaced with
``"***"`` before rendering, whatever its value. ``SecretStr`` values render masked anyway.
"""

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

_SECRET_KEY = re.compile(r"pass(word|wd)?|secret|token|api[_-]?key|credential", re.IGNORECASE)
REDACTED = "***"


def redact_secrets(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: mask values of secret-looking keys (recursing into dicts)."""

    def scrub(mapping: MutableMapping[str, Any]) -> None:
        for key, value in mapping.items():
            if _SECRET_KEY.search(str(key)):
                mapping[key] = REDACTED
            elif isinstance(value, MutableMapping):
                scrub(value)

    scrub(event_dict)
    return event_dict


def configure_logging(level: str = "INFO", *, json: bool = True) -> None:
    """Configure structlog for the process: ISO-8601 UTC timestamps, JSON lines to stderr."""
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            redact_secrets,
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(file=_CurrentStderr()),  # type: ignore[arg-type]
        cache_logger_on_first_use=True,
    )


class _CurrentStderr:
    """Writes to whatever ``sys.stderr`` is *now*. Cached loggers outlive redirections (test
    runners, CLI harnesses); binding the stream object at configure time would leave them
    writing to a closed file."""

    def write(self, text: str) -> int:
        return sys.stderr.write(text)

    def flush(self) -> None:
        sys.stderr.flush()
