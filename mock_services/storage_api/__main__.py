"""Run the mock storage service standalone: ``python -m mock_services.storage_api``."""

from __future__ import annotations

import uvicorn


def main() -> None:
    """Serve the storage mock on 127.0.0.1:9100."""
    uvicorn.run(
        "mock_services.storage_api.app:app",
        host="127.0.0.1",
        port=9100,
        log_level="warning",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
