"""Run the mock storage service standalone: ``python -m mock_services.storage_api``.

Port defaults to 9100 and can be overridden via the ``STORAGE_PORT``
environment variable, matched by ``scripts/run_mocks.sh`` so the
readiness probe and the child share the same port. Host defaults to
``127.0.0.1`` and can be overridden via ``STORAGE_HOST`` which the Docker
Compose stack sets to ``0.0.0.0``.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    """Serve the storage mock on ${STORAGE_HOST:-127.0.0.1}:${STORAGE_PORT:-9100}."""
    host = os.getenv("STORAGE_HOST", "127.0.0.1")
    port = int(os.getenv("STORAGE_PORT", "9100"))
    uvicorn.run(
        "mock_services.storage_api.app:app",
        host=host,
        port=port,
        log_level="warning",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
