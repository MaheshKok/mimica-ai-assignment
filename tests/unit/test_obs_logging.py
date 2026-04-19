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

import structlog

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
