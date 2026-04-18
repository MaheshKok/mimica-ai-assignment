"""Tests for :mod:`app.main` — factory, lifespan, and TimeoutError mapping.

The TimeoutError handler fires when anything inside the orchestrator (or a
port it calls) raises :class:`TimeoutError`. Wiring a port that raises the
error lets us verify the 504 envelope without needing Phase 4's
``asyncio.timeout`` wrapper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.relevance_fake import FakeRelevanceRanker
from app.adapters.storage_fake import FakeScreenshotStorage
from app.core.models import ScreenshotRef
from app.deps import Ports, get_ports, get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

VALID_UUID = str(uuid4())


@dataclass
class _TimingOutWorkflow:
    """Workflow whose qa_answer raises TimeoutError."""

    refs: list[ScreenshotRef] = field(default_factory=list)

    def stream_project(self, _project_id: UUID) -> AsyncIterator[ScreenshotRef]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[ScreenshotRef]:
        for ref in self.refs:
            yield ref

    async def qa_answer(self, _question: str, _ids: list[str]) -> str:
        raise TimeoutError("simulated total-budget timeout")


@pytest.fixture
def client() -> TestClient:
    from app.main import app

    app.dependency_overrides.clear()
    app.dependency_overrides[get_settings] = _overridden_settings
    return TestClient(app)


def _overridden_settings() -> object:
    from app.config import Settings

    return Settings(_env_file=None)  # type: ignore[arg-type]


def _install(ports: Ports) -> None:
    from app.main import app

    app.dependency_overrides[get_ports] = lambda: ports


class TestTimeoutMapping:
    def test_timeout_error_maps_to_504(self, client: TestClient) -> None:
        refs = [ScreenshotRef(timestamp=1, image_id="a")]
        _install(
            Ports(
                workflow=_TimingOutWorkflow(refs=refs),
                storage=FakeScreenshotStorage(images={"a": b"x"}),
                relevance=FakeRelevanceRanker(),
            )
        )
        r = client.post(
            "/enriched-qa",
            json={
                "project_id": VALID_UUID,
                "from": 0,
                "to": 100,
                "question": "q",
            },
        )
        assert r.status_code == 504
        body = r.json()
        assert body["error"] == "request_timeout"
        assert "timeout" in body["detail"].lower()
        assert body["request_id"]


class TestLifespan:
    def test_context_runs_startup_and_shutdown(self) -> None:
        """``with TestClient(app)`` exercises the lifespan enter + exit path."""
        from app.main import app

        with TestClient(app):
            # If lifespan raises, the context manager propagates here.
            pass

    def test_wires_real_http_adapters_and_resources(self) -> None:
        """Lifespan must construct the HTTP adapters, not accidentally ship fakes.

        A regression that silently reverted to ``build_demo_ports`` would
        pass the "doesn't raise" smoke check. This test asserts the
        concrete adapter classes, the presence of the process-wide
        semaphore, and that the http client is open during the request.
        """
        import asyncio

        from app.adapters.storage_http import HttpxScreenshotStorageClient
        from app.adapters.workflow_http import HttpxWorkflowServicesClient
        from app.main import app

        with TestClient(app):
            assert isinstance(app.state.ports.workflow, HttpxWorkflowServicesClient), (
                "lifespan must wire the HTTP workflow adapter, not a fake"
            )
            assert isinstance(app.state.ports.storage, HttpxScreenshotStorageClient), (
                "lifespan must wire the HTTP storage adapter, not a fake"
            )
            assert isinstance(app.state.global_fetch_semaphore, asyncio.Semaphore), (
                "process-wide storage semaphore must be on app.state"
            )
            assert not app.state.http_client.is_closed, (
                "http client must be open while the lifespan context is active"
            )

    def test_closes_http_client_on_shutdown(self) -> None:
        """Lifespan's finally branch must ``aclose()`` the shared client.

        Without this, rapid reloads/tests leak transports. Assertion is
        checked *after* the TestClient context exits so we know the
        shutdown path actually ran.
        """
        from app.main import app

        with TestClient(app):
            client = app.state.http_client
            assert not client.is_closed
        assert client.is_closed, "lifespan must close the shared http client on shutdown"


class TestRootFactory:
    def test_create_app_produces_fresh_instance_each_call(self) -> None:
        from app.main import create_app

        first = create_app()
        second = create_app()
        assert first is not second
        # Both should expose the route we care about.
        assert "/enriched-qa" in {r.path for r in first.routes}  # type: ignore[attr-defined]
        assert "/enriched-qa" in {r.path for r in second.routes}  # type: ignore[attr-defined]
