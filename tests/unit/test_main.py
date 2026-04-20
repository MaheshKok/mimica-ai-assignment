"""Tests for :mod:`app.main` — factory, lifespan, and TimeoutError mapping.

The TimeoutError handler fires when anything inside the orchestrator (or
a port it calls) raises :class:`TimeoutError`. Wiring a port that raises
the error lets us verify the 504 envelope in isolation - without
spinning up the real ``asyncio.timeout`` wrapper from the route.
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

    def test_wires_cpu_relevance_ranker_and_process_pool(self) -> None:
        """Lifespan must wire the CPU ranker with the process pool it owns.

        Without this assertion, a regression that reverted to
        ``FakeRelevanceRanker`` (or never swapped it in) would pass the
        live-stack 200 test - the fake produces the same response shape.
        Asserts the ranker type, that the pool injected into the ranker
        is the same instance stashed on ``app.state``, and that
        ``max_input`` matches the configured ``max_rank_input``.
        """
        from concurrent.futures import ProcessPoolExecutor

        from app.adapters.relevance_cpu import CpuRelevanceRanker
        from app.main import app

        with TestClient(app):
            ranker = app.state.ports.relevance
            assert isinstance(ranker, CpuRelevanceRanker), (
                f"lifespan must wire CpuRelevanceRanker, got {type(ranker).__name__}"
            )
            assert isinstance(app.state.process_pool, ProcessPoolExecutor)
            assert ranker.pool is app.state.process_pool, (
                "ranker must reference the lifespan-owned pool, not a private one"
            )
            assert ranker.max_input == app.state.settings.max_rank_input

    def test_lifespan_configures_and_shuts_down_observability(self) -> None:
        """Lifespan must configure tracing on startup and tear it down on exit.

        A regression that removed ``obs_tracing.configure`` from the
        lifespan would leave the service with OTel's proxy provider, so
        manual spans in the orchestrator fall on the floor. Checking the
        module flag is a compact way to assert the wiring without
        reaching into the exporter's internals.
        """
        from app.main import app
        from app.observability import tracing as tracing_mod

        with TestClient(app):
            assert tracing_mod._configured, "lifespan must call obs_tracing.configure(settings)"
        # The lifespan finally branch must call shutdown(), otherwise
        # batched spans are lost when the worker exits and the next
        # configure cannot install a fresh provider.
        assert not tracing_mod._configured, "lifespan must call obs_tracing.shutdown() on exit"

    def test_request_id_middleware_installed(self) -> None:
        """Route must respond with X-Request-Id header - middleware wired."""
        from app.main import app

        with TestClient(app) as client:
            # Use an invalid payload so the request is cheap to process.
            r = client.post(
                "/enriched-qa",
                json={"project_id": "bad", "from": 0, "to": 1, "question": "q"},
            )
            assert r.headers.get("x-request-id"), (
                "every response must carry x-request-id; middleware missing?"
            )

    def test_shuts_down_process_pool_on_exit(self) -> None:
        """Lifespan's finally branch must shut the pool down.

        A submit-after-exit must raise - proves ``shutdown(wait=True)``
        actually ran. Without this, a slow rank lingering past teardown
        could keep workers and sockets alive across reloads.
        """
        from app.main import app

        with TestClient(app):
            pool = app.state.process_pool
        with pytest.raises(RuntimeError):
            pool.submit(pow, 2, 3)


class TestRelevanceRankerErrorHandler:
    """Route handler must map ``RelevanceRankerError`` to HTTP 503.

    Covers the path a broken process pool takes through the adapter:
    orchestrator propagates the exception, the handler returns a 503
    envelope with error=``relevance_ranker_unavailable``, preserving
    the request_id and the cause in ``detail``.
    """

    def test_relevance_ranker_error_returns_503_envelope(self) -> None:
        from app.core.errors import RelevanceRankerError
        from app.main import app

        class _ExplodingRanker:
            async def rank(self, screenshots: object, question: str, top_k: int) -> list[str]:
                raise RelevanceRankerError(RuntimeError("pool is broken"))

        def _override_ports() -> Ports:
            return Ports(
                workflow=_WorkflowThatStreamsOneRef(),
                storage=FakeScreenshotStorage(
                    images={"img-1.png": b"bytes"},
                ),
                relevance=_ExplodingRanker(),  # type: ignore[arg-type]
            )

        app.dependency_overrides[get_ports] = _override_ports
        try:
            with TestClient(app) as client:
                r = client.post(
                    "/enriched-qa",
                    json={
                        "project_id": str(uuid4()),
                        "from": 1_700_000_000,
                        "to": 1_700_001_000,
                        "question": "what is happening?",
                    },
                )
            assert r.status_code == 503, r.text
            body = r.json()
            assert body["error"] == "relevance_ranker_unavailable"
            assert "pool is broken" in body["detail"]
            assert UUID(body["request_id"])
        finally:
            app.dependency_overrides.clear()


@dataclass
class _WorkflowThatStreamsOneRef:
    """Minimal workflow stub yielding one ref in the requested window."""

    qa_calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    async def stream_project(self, project_id: UUID) -> AsyncIterator[ScreenshotRef]:
        yield ScreenshotRef(timestamp=1_700_000_500, image_id="img-1.png")

    async def qa_answer(self, question: str, relevant_images: list[str]) -> str:
        self.qa_calls.append((question, tuple(relevant_images)))
        return "ok"


class TestRootFactory:
    def test_create_app_produces_fresh_instance_each_call(self) -> None:
        from app.main import create_app

        first = create_app()
        second = create_app()
        assert first is not second
        # Both should expose the route we care about.
        assert "/enriched-qa" in {r.path for r in first.routes}  # type: ignore[attr-defined]
        assert "/enriched-qa" in {r.path for r in second.routes}  # type: ignore[attr-defined]
