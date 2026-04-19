"""Run the mock workflow service standalone: ``python -m mock_services.workflow_api``."""

from __future__ import annotations

import uvicorn


def main() -> None:
    """Serve the workflow mock on 127.0.0.1:9000."""
    uvicorn.run(
        "mock_services.workflow_api.app:app",
        host="127.0.0.1",
        port=9000,
        log_level="warning",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
