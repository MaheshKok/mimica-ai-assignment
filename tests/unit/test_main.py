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
        """`with TestClient(app)` exercises the lifespan enter + exit path."""
        from app.main import app

        with TestClient(app):
            # If lifespan raises, the context manager propagates here.
            pass


class TestRootFactory:
    def test_create_app_produces_fresh_instance_each_call(self) -> None:
        from app.main import create_app

        first = create_app()
        second = create_app()
        assert first is not second
        # Both should expose the route we care about.
        assert "/enriched-qa" in {r.path for r in first.routes}  # type: ignore[attr-defined]
        assert "/enriched-qa" in {r.path for r in second.routes}  # type: ignore[attr-defined]
