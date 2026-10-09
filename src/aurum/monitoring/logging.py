"""Structured JSON logging (structlog) with secret redaction.

Any event key that looks like a secret (password, secret, token, api key) is replaced with
``"***"`` before rendering, whatever its value. ``SecretStr`` values render masked anyway.
"""

import logging
import re
import sys
from collections.abc import MutableMapping
from pathlib import Path
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


def configure_logging(level: str = "INFO", *, json: bool = True, file: Path | None = None) -> None:
    """Configure structlog for the process: ISO-8601 UTC timestamps, JSON lines to stderr, or
    appended to ``file`` (for unattended runs such as the collector)."""
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
        logger_factory=structlog.PrintLoggerFactory(
            file=_CurrentStderr() if file is None else _AppendFile(file)  # type: ignore[arg-type]
        ),
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


class _AppendFile:
    """Appends each log line to ``path``, opening and closing it per line: nothing is lost on a
    hard kill and no handle is held (log volume is low: heartbeats, flushes, events)."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = path

    def write(self, text: str) -> int:
        with self._path.open("a", encoding="utf-8") as f:
            return f.write(text)

    def flush(self) -> None:
        pass
