"""Tests for mock-service module entrypoints.

These guard the local-vs-container binding contract: the standalone mock
services should default to 127.0.0.1 for local scripts, but allow Docker to
override the host to 0.0.0.0 through environment variables.
"""

from __future__ import annotations

from unittest.mock import patch


def test_workflow_entrypoint_defaults_to_loopback() -> None:
    from mock_services.workflow_api import __main__ as workflow_main

    with patch("uvicorn.run") as run:
        workflow_main.main()
    run.assert_called_once_with(
        "mock_services.workflow_api.app:app",
        host="127.0.0.1",
        port=9000,
        log_level="warning",
    )


def test_workflow_entrypoint_allows_host_override(monkeypatch) -> None:
    from mock_services.workflow_api import __main__ as workflow_main

    monkeypatch.setenv("WORKFLOW_HOST", "0.0.0.0")
    monkeypatch.setenv("WORKFLOW_PORT", "9011")
    with patch("uvicorn.run") as run:
        workflow_main.main()
    run.assert_called_once_with(
        "mock_services.workflow_api.app:app",
        host="0.0.0.0",
        port=9011,
        log_level="warning",
    )


def test_storage_entrypoint_defaults_to_loopback() -> None:
    from mock_services.storage_api import __main__ as storage_main

    with patch("uvicorn.run") as run:
        storage_main.main()
    run.assert_called_once_with(
        "mock_services.storage_api.app:app",
        host="127.0.0.1",
        port=9100,
        log_level="warning",
    )


def test_storage_entrypoint_allows_host_override(monkeypatch) -> None:
    from mock_services.storage_api import __main__ as storage_main

    monkeypatch.setenv("STORAGE_HOST", "0.0.0.0")
    monkeypatch.setenv("STORAGE_PORT", "9111")
    with patch("uvicorn.run") as run:
        storage_main.main()
    run.assert_called_once_with(
        "mock_services.storage_api.app:app",
        host="0.0.0.0",
        port=9111,
        log_level="warning",
    )
