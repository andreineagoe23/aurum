"""Unit tests for structured logging and secret redaction."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog

from aurum.monitoring.logging import configure_logging, redact_secrets


@pytest.fixture(autouse=True)
def _reset_structlog() -> Iterator[None]:
    yield
    structlog.reset_defaults()


def _redact(event: dict) -> dict:  # type: ignore[type-arg]
    return dict(redact_secrets(None, "info", event))


def test_secret_keys_masked() -> None:
    for key in ["password", "mt5_password", "MT5_PASSWORD", "api_key", "token"]:
        out = _redact({key: "hunter2", "event": "e"})
        assert (out[key], out["event"]) == ("***", "e"), key
    out = _redact({"cfg": {"secret": "s3", "host": "h"}})
    assert out["cfg"] == {"secret": "***", "host": "h"}  # nested
    untouched = {"login": 123, "server": "demo", "event": "x"}
    assert _redact(dict(untouched)) == untouched


def test_end_to_end_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(json=True)
    structlog.get_logger().info("evt", password="hunter2", login=123)
    err = capsys.readouterr().err
    assert "hunter2" not in err
    data = json.loads(err.strip().splitlines()[-1])
    assert data["login"] == 123
    assert data["password"] == "***"
    assert data["event"] == "evt"


def test_logs_can_go_to_a_file(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "collector.jsonl"
    configure_logging(file=path)
    structlog.get_logger("t").info("collector.heartbeat", password="x", rows=3)
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert record["event"] == "collector.heartbeat"
    assert record["rows"] == 3
    assert record["password"] == "***"
