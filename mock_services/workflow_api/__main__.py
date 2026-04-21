"""Run the mock workflow service standalone: ``python -m mock_services.workflow_api``.

Port defaults to 9000 and can be overridden via the ``WORKFLOW_PORT``
environment variable, which ``scripts/run_mocks.sh`` plumbs through
for readiness probes and ephemeral-port test fixtures. Host defaults to
``127.0.0.1`` and can be overridden via ``WORKFLOW_HOST`` which the Docker
Compose stack sets to ``0.0.0.0``.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    """Serve the workflow mock on ${WORKFLOW_HOST:-127.0.0.1}:${WORKFLOW_PORT:-9000}."""
    host = os.getenv("WORKFLOW_HOST", "127.0.0.1")
    port = int(os.getenv("WORKFLOW_PORT", "9000"))
    uvicorn.run(
        "mock_services.workflow_api.app:app",
        host=host,
        port=port,
        log_level="warning",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
