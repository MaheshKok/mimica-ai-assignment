"""FastAPI wire-up tests for ``POST /enriched-qa``.

Focuses on what the route layer owns that unit tests of the orchestrator
cannot see:

- Pydantic's default 422 is overridden to 400 by the handler.
- Domain errors raised inside the orchestrator are mapped to the
  ``{error, detail, request_id}`` envelope from ``architect.md`` §7.
- The request body's literal ``"from"`` key round-trips through FastAPI.

Uses ``app.dependency_overrides`` to swap in fakes that trigger each branch.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from app.adapters.relevance_fake import FakeRelevanceRanker
from app.adapters.storage_fake import FakeScreenshotStorage
from app.adapters.workflow_fake import FakeWorkflowServicesClient
from app.core.errors import WorkflowUpstreamError
from app.core.models import ScreenshotRef
from app.deps import Ports, get_ports, get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

VALID_UUID = str(uuid4())


@pytest.fixture
def client() -> TestClient:
    """Fresh TestClient with dependency overrides reset per test."""
    # Import inside the fixture so each test constructs against the live app;
    # avoids cross-test pollution in app.dependency_overrides.
    from app.main import app

    app.dependency_overrides.clear()
    app.dependency_overrides[get_settings] = _overridden_settings
    return TestClient(app)


def _overridden_settings() -> object:
    from app.config import Settings

    return Settings(_env_file=None)  # type: ignore[arg-type]


def _install_ports(ports: Ports) -> None:
    from app.main import app

    app.dependency_overrides[get_ports] = lambda: ports


# --------------------------------------------------------------------------- #
# Happy-path wire-up                                                          #
# --------------------------------------------------------------------------- #


class TestHappyPath:
    def test_literal_from_key_returns_200(self, client: TestClient) -> None:
        refs = [ScreenshotRef(timestamp=50, image_id="a")]
        _install_ports(
            Ports(
                workflow=FakeWorkflowServicesClient(refs=refs, canned_answer="from-wire"),
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
        assert r.status_code == 200
        body = r.json()
        assert body["answer"] == "from-wire"
        assert body["meta"]["images_considered"] == 1
        assert body["meta"]["images_relevant"] == 1

    def test_response_includes_request_id(self, client: TestClient) -> None:
        _install_ports(
            Ports(
                workflow=FakeWorkflowServicesClient(),
                storage=FakeScreenshotStorage(),
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
        assert r.status_code == 200
        assert isinstance(r.json()["meta"]["request_id"], str)
        assert r.json()["meta"]["request_id"]  # non-empty


# --------------------------------------------------------------------------- #
# Validation mapping (422 -> 400)                                             #
# --------------------------------------------------------------------------- #


class TestValidationMapping:
    def test_from_equal_to_returns_400(self, client: TestClient) -> None:
        _install_ports(
            Ports(
                workflow=FakeWorkflowServicesClient(),
                storage=FakeScreenshotStorage(),
                relevance=FakeRelevanceRanker(),
            )
        )
        r = client.post(
            "/enriched-qa",
            json={
                "project_id": VALID_UUID,
                "from": 10,
                "to": 10,
                "question": "q",
            },
        )
        assert r.status_code == 400, "from == to must be 400 (handler overrides 422)"
        body = r.json()
        assert body["error"] == "invalid_request"
        assert "detail" in body
        assert "request_id" in body

    def test_missing_question_returns_400(self, client: TestClient) -> None:
        _install_ports(
            Ports(
                workflow=FakeWorkflowServicesClient(),
                storage=FakeScreenshotStorage(),
                relevance=FakeRelevanceRanker(),
            )
        )
        r = client.post(
            "/enriched-qa",
            json={
                "project_id": VALID_UUID,
                "from": 0,
                "to": 100,
            },
        )
        assert r.status_code == 400

    def test_empty_question_returns_400(self, client: TestClient) -> None:
        _install_ports(
            Ports(
                workflow=FakeWorkflowServicesClient(),
                storage=FakeScreenshotStorage(),
                relevance=FakeRelevanceRanker(),
            )
        )
        r = client.post(
            "/enriched-qa",
            json={
                "project_id": VALID_UUID,
                "from": 0,
                "to": 100,
                "question": "",
            },
        )
        assert r.status_code == 400

    def test_invalid_uuid_returns_400(self, client: TestClient) -> None:
        _install_ports(
            Ports(
                workflow=FakeWorkflowServicesClient(),
                storage=FakeScreenshotStorage(),
                relevance=FakeRelevanceRanker(),
            )
        )
        r = client.post(
            "/enriched-qa",
            json={
                "project_id": "not-a-uuid",
                "from": 0,
                "to": 100,
                "question": "q",
            },
        )
        assert r.status_code == 400


# --------------------------------------------------------------------------- #
# Error envelope mapping                                                      #
# --------------------------------------------------------------------------- #


@dataclass
class _FailingWorkflow:
    """Workflow whose qa_answer raises WorkflowUpstreamError."""

    refs: list[ScreenshotRef] = field(default_factory=list)

    def stream_project(self, _project_id: UUID) -> AsyncIterator[ScreenshotRef]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[ScreenshotRef]:
        for ref in self.refs:
            yield ref

    async def qa_answer(self, _question: str, _ids: list[str]) -> str:
        raise WorkflowUpstreamError(RuntimeError("upstream-dead"))


class TestErrorEnvelope:
    def test_partial_failure_threshold_maps_to_502(self, client: TestClient) -> None:
        refs = [ScreenshotRef(timestamp=i, image_id=f"img-{i}") for i in range(10)]
        _install_ports(
            Ports(
                workflow=FakeWorkflowServicesClient(refs=refs, canned_answer="never-reached"),
                # 8/10 missing -> 80% failure, well past default 20% threshold
                storage=FakeScreenshotStorage(
                    images={r.image_id: b"x" for r in refs},
                    missing={f"img-{i}" for i in range(8)},
                ),
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
        assert r.status_code == 502
        body = r.json()
        assert body["error"] == "storage_partial_failure"
        assert "8/10" in body["detail"] or "8" in body["detail"]
        assert body["request_id"]

    def test_workflow_upstream_error_maps_to_502(self, client: TestClient) -> None:
        refs = [ScreenshotRef(timestamp=1, image_id="a")]
        _install_ports(
            Ports(
                workflow=_FailingWorkflow(refs=refs),
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
        assert r.status_code == 502
        body = r.json()
        assert body["error"] == "workflow_upstream_failure"
        assert body["request_id"]

    def test_error_envelope_shape_is_consistent(self, client: TestClient) -> None:
        # All error responses must have exactly {error, detail, request_id}.
        _install_ports(
            Ports(
                workflow=FakeWorkflowServicesClient(),
                storage=FakeScreenshotStorage(),
                relevance=FakeRelevanceRanker(),
            )
        )
        r = client.post(
            "/enriched-qa",
            json={
                "project_id": VALID_UUID,
                "from": 10,
                "to": 5,  # invalid window
                "question": "q",
            },
        )
        assert r.status_code == 400
        assert set(r.json().keys()) == {"error", "detail", "request_id"}
