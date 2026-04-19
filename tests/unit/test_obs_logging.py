"""Tests for :mod:`app.observability.logging`.

Contract (derived from the module docstring + structlog API):

- ``configure`` makes :func:`structlog.get_logger` emit a single JSON
  object per call.
- Bindings made with :func:`structlog.contextvars.bind_contextvars` end
  up inside that JSON object (so the middleware's ``request_id`` flows
  into every log line for the duration of a request).
- Log records have a level and a timestamp.
- ``configure`` is idempotent - two calls do not stack processors into
  duplicate output lines.
"""

from __future__ import annotations

import io
import json
import logging
from contextlib import redirect_stdout
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import pytest

from app.observability.logging import configure


def _capture_log(name: str, **bindings: object) -> dict[str, object]:
    """Emit one INFO log line and parse it back as JSON."""
    logger = structlog.get_logger("test-logger")
    buf = io.StringIO()
    structlog.contextvars.clear_contextvars()
    with redirect_stdout(buf), structlog.contextvars.bound_contextvars(**bindings):
        logger.info(name, some_field="value")
    line = buf.getvalue().strip()
    return json.loads(line)


class TestConfigureRendersJson:
    def test_emits_valid_json_per_call(self) -> None:
        configure()
        payload = _capture_log("hello")
        assert isinstance(payload, dict)
        assert payload["event"] == "hello"

    def test_includes_log_level(self) -> None:
        configure()
        payload = _capture_log("levelled")
        assert payload["level"] == "info"

    def test_includes_timestamp(self) -> None:
        configure()
        payload = _capture_log("stamped")
        # ISO-8601 UTC - ends with 'Z' or has an explicit offset.
        assert "timestamp" in payload
        assert isinstance(payload["timestamp"], str)

    def test_includes_user_bindings(self) -> None:
        configure()
        payload = _capture_log("bound", request_id="abc-123")
        assert payload["request_id"] == "abc-123"

    def test_log_body_kwargs_flow_into_json(self) -> None:
        configure()
        payload = _capture_log("with-fields")
        assert payload["some_field"] == "value"


class TestConfigureIdempotent:
    def test_calling_twice_still_produces_one_line(self) -> None:
        """A second ``configure`` must not double-write log records."""
        configure()
        configure()
        logger = structlog.get_logger("idempotent-test")
        buf = io.StringIO()
        structlog.contextvars.clear_contextvars()
        with redirect_stdout(buf):
            logger.info("once")
        # One JSON line: one newline-terminated string.
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        assert len(lines) == 1


class TestStdlibLoggingRouted:
    def test_configure_sets_stdlib_root_level(self) -> None:
        """Stdlib logging's root level should match the structlog level."""
        configure(level=logging.WARNING)
        try:
            assert logging.getLogger().level == logging.WARNING
        finally:
            configure()  # restore INFO default

    def test_stdlib_warning_emits_json_with_request_id(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Stdlib log records must be JSON-formatted and include bound contextvars.

        This is the regression gate for the ProcessorFormatter fix: if stdlib
        logging is only wired via ``basicConfig(format="%(message)s")`` the
        record is plain text and request_id is absent.

        The stdlib StreamHandler stores a reference to sys.stdout at configure()
        time, which inside a pytest test is pytest's own capture buffer.
        ``capsys.readouterr()`` reads from that buffer, so it correctly
        intercepts what the StreamHandler wrote (unlike ``redirect_stdout``
        which changes the name ``sys.stdout`` but not the stored reference).
        """
        configure()
        structlog.contextvars.clear_contextvars()
        with structlog.contextvars.bound_contextvars(request_id="rid-stdlib-001"):
            logging.getLogger("test.adapter").warning("malformed ndjson line")
        captured = capsys.readouterr()
        line = captured.out.strip()
        assert line, "stdlib log must produce JSON output on stdout"
        parsed = json.loads(line)
        assert parsed.get("request_id") == "rid-stdlib-001"
        assert parsed.get("level") in {"warning", "warn"}
        assert "event" in parsed or "message" in parsed
