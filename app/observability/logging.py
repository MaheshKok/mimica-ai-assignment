"""structlog JSON logging for the Enriched QA Service.

Configures structlog to emit one JSON object per log line on stdout, with
ISO-8601 timestamps, log levels, logger names, and any bindings stashed in
``structlog.contextvars``. The request-id middleware in
:mod:`app.observability.middleware` binds ``request_id`` there so every
log line emitted during a request carries the same correlation id that
the response envelope and spans do.

``configure`` is idempotent: calling it twice (e.g. app reloads in tests)
replaces the existing processor chain rather than stacking processors.
"""

from __future__ import annotations

import logging

import structlog


def configure(*, level: int = logging.INFO) -> None:
    """Configure structlog and the stdlib ``logging`` module.

    The processor chain merges ``structlog.contextvars`` (so middleware-set
    ``request_id`` and OTel-injected ``trace_id``/``span_id`` flow into
    every log line), stamps the level and timestamp, and renders JSON.
    Stdlib ``logging`` is configured in the same call so adapter modules
    that use ``logging.getLogger(__name__)`` (e.g.
    :mod:`app.adapters.workflow_http`) share the same JSON output.

    Args:
        level: Root log level. Defaults to :data:`logging.INFO`.
    """
    processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    # Route stdlib logging through the same JSON formatter so third-party
    # libraries (httpx, uvicorn, our adapters) emit the same shape.
    logging.basicConfig(level=level, format="%(message)s", force=True)
