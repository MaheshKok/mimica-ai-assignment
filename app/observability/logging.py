"""structlog JSON logging for the Enriched QA Service.

Configures structlog to emit one JSON object per log line on stdout, with
ISO-8601 timestamps, log levels, logger names, and any bindings stashed in
``structlog.contextvars``. The request-id middleware in
:mod:`app.observability.middleware` binds ``request_id`` there so every
log line emitted during a request carries the same correlation id that
the response envelope and spans do.

Stdlib ``logging`` records (from httpx, uvicorn, and adapter modules that
use ``logging.getLogger(__name__)``) are routed through
``structlog.stdlib.ProcessorFormatter`` so they share the same JSON shape
and have access to the same contextvars — including ``request_id`` — rather
than emitting plain-text messages that bypass correlation.

``configure`` is idempotent: calling it twice (e.g. app reloads in tests)
replaces the existing processor chain rather than stacking processors.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure(*, level: int = logging.INFO) -> None:
    """Configure structlog and route stdlib logging through the same JSON pipeline.

    The structlog processor chain merges ``structlog.contextvars`` (so
    middleware-set ``request_id`` flows into every log line), stamps the
    level and timestamp, and renders JSON. Stdlib ``logging`` is handled by
    a ``structlog.stdlib.ProcessorFormatter`` attached to the root logger so
    third-party loggers — httpx, uvicorn access log, adapter modules using
    ``logging.getLogger(__name__)`` — emit the same JSON shape and include
    the same contextvars.

    Args:
        level: Root log level for both structlog and stdlib. Defaults to
            :data:`logging.INFO`.
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    # Route stdlib logging through structlog.stdlib.ProcessorFormatter so that
    # third-party loggers (httpx, uvicorn, app adapters using
    # logging.getLogger) emit JSON and include contextvars like request_id.
    # ProcessorFormatter applies foreign_pre_chain to stdlib LogRecords before
    # the final renderer, giving them access to merge_contextvars.
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)
