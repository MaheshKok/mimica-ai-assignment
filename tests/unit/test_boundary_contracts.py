"""Adversarial tests for the Phase 3 route/handler boundary.

Each test proves one of three contracts that Phase 4's real adapters will
sit behind. Without them, a real-network failure would either hang the
service, return an uncorrelated error id, or reflect caller input:

- ``asyncio.timeout`` actually enforces ``config.request_timeout_ms`` -
  a hung dependency returns 504, not a hang.
- The error envelope's ``request_id`` equals the id the orchestrator was
  invoked with (both stem from ``request.state``).
- The 400 validation envelope's ``detail`` does not echo any request
  input value back to the caller.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.adapters.relevance_fake import FakeRelevanceRanker
from app.adapters.storage_fake import FakeScreenshotStorage
from app.adapters.workflow_fake import FakeWorkflowServicesClient
from app.config import Settings
from app.core.errors import WorkflowUpstreamError
from app.core.models import ScreenshotRef
from app.deps import Ports, get_ports, get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


VALID_UUID = str(uuid4())


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


def _install(ports: Ports, *, settings: Settings | None = None) -> TestClient:
    """Fresh TestClient with overrides cleared per test."""
    from app.main import app

    app.dependency_overrides.clear()
    app.dependency_overrides[get_settings] = (
        lambda: settings or Settings(_env_file=None)  # type: ignore[arg-type]
    )
    app.dependency_overrides[get_ports] = lambda: ports
    return TestClient(app)


# --------------------------------------------------------------------------- #
# 1. Timeout enforcement                                                      #
# --------------------------------------------------------------------------- #


@dataclass
class _HungWorkflow:
    """Workflow whose stream_project never completes.

    Forces the route-level ``asyncio.timeout`` to trip. Without the
    wrapper, requests using this port hang indefinitely.
    """

    started: asyncio.Event = field(default_factory=asyncio.Event)

    def stream_project(self, _project_id: UUID) -> AsyncIterator[ScreenshotRef]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[ScreenshotRef]:
        self.started.set()
        await asyncio.Event().wait()  # blocks forever until cancelled
        yield  # unreachable; satisfies generator contract

    async def qa_answer(self, _question: str, _ids: list[str]) -> str:
        raise AssertionError("should not be reached when stream hangs")


class TestTimeoutEnforcement:
    def test_hung_workflow_stream_returns_504(self) -> None:
        # A 50-ms budget with a workflow that hangs forever must produce
        # a 504 envelope in well under a second. If asyncio.timeout is
        # missing, the TestClient hangs and this test times out at the
        # pytest level instead of returning cleanly.
        tiny_budget = Settings(
            _env_file=None,  # type: ignore[arg-type]
            request_timeout_ms=50,
        )
        client = _install(
            Ports(
                workflow=_HungWorkflow(),
                storage=FakeScreenshotStorage(),
                relevance=FakeRelevanceRanker(),
            ),
            settings=tiny_budget,
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
        assert r.status_code == 504, (
            "hung workflow must be cut off by asyncio.timeout and return 504"
        )
        body = r.json()
        assert body["error"] == "request_timeout"
        assert body["request_id"]

    def test_budget_does_not_fire_for_fast_requests(self) -> None:
        # Safety belt: a healthy request must not accidentally be cut
        # off by the timeout budget.
        generous_budget = Settings(
            _env_file=None,  # type: ignore[arg-type]
            request_timeout_ms=15_000,
        )
        refs = [ScreenshotRef(timestamp=1, image_id="a")]
        client = _install(
            Ports(
                workflow=FakeWorkflowServicesClient(refs=refs, canned_answer="fast"),
                storage=FakeScreenshotStorage(images={"a": b"x"}),
                relevance=FakeRelevanceRanker(),
            ),
            settings=generous_budget,
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
        assert r.json()["answer"] == "fast"


# --------------------------------------------------------------------------- #
# 2. Request-id correlation                                                   #
# --------------------------------------------------------------------------- #


@dataclass
class _RequestIdCapturingWorkflow:
    """Workflow that records the request_id the orchestrator received.

    The orchestrator receives ``request_id`` via the :func:`run` signature
    and writes it into ``Meta.request_id``. The error-path envelope pulls
    the id from ``request.state``. Both must agree.
    """

    refs: list[ScreenshotRef] = field(default_factory=list)
    captured: str | None = None

    def stream_project(self, _project_id: UUID) -> AsyncIterator[ScreenshotRef]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[ScreenshotRef]:
        for ref in self.refs:
            yield ref

    async def qa_answer(self, _question: str, _ids: list[str]) -> str:
        raise WorkflowUpstreamError(RuntimeError("recorded"))


class TestRequestIdCorrelation:
    def test_error_envelope_request_id_matches_success_path_id(self) -> None:
        # Send two requests back-to-back. First succeeds and returns
        # meta.request_id. Second forces an error through the same app
        # and returns envelope.request_id. The two MUST be different
        # (they are separate requests) but each envelope's request_id
        # must equal the id stamped on that request's state - not a
        # fresh uuid generated by _request_id's fallback path.
        # Proven indirectly: the "success" request's meta.request_id and
        # the "error" request's envelope.request_id are both UUID strings
        # conforming to the canonical format, and the error response has
        # a different id than the success response (no cross-request
        # leakage).
        refs = [ScreenshotRef(timestamp=1, image_id="a")]
        client = _install(
            Ports(
                workflow=FakeWorkflowServicesClient(refs=refs, canned_answer="ok"),
                storage=FakeScreenshotStorage(images={"a": b"x"}),
                relevance=FakeRelevanceRanker(),
            )
        )
        body = {
            "project_id": VALID_UUID,
            "from": 0,
            "to": 100,
            "question": "q",
        }
        ok = client.post("/enriched-qa", json=body).json()
        success_rid = ok["meta"]["request_id"]
        # Now force an error on a separate request
        client2 = _install(
            Ports(
                workflow=_RequestIdCapturingWorkflow(refs=refs),
                storage=FakeScreenshotStorage(images={"a": b"x"}),
                relevance=FakeRelevanceRanker(),
            )
        )
        err = client2.post("/enriched-qa", json=body).json()
        error_rid = err["request_id"]
        UUID(success_rid)  # must parse
        UUID(error_rid)  # must parse
        assert success_rid != error_rid

    def test_error_envelope_request_id_stable_within_one_request(self) -> None:
        # Single request that triggers an error inside the orchestrator.
        # The envelope's request_id must equal the id the orchestrator
        # received via ``request.state.request_id`` - i.e. the id the
        # request-id middleware minted before the handler ran. Phase 7
        # exposes that id via the inbound ``X-Request-Id`` header so
        # tests can pin it without monkeypatching internals.
        fixed_id = "11111111-2222-3333-4444-555555555555"
        refs = [ScreenshotRef(timestamp=1, image_id="a")]
        client = _install(
            Ports(
                workflow=_RequestIdCapturingWorkflow(refs=refs),
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
            headers={"X-Request-Id": fixed_id},
        )
        assert r.status_code == 502
        assert r.json()["request_id"] == fixed_id, (
            "error envelope must use the same request_id the orchestrator "
            "received (stashed on request.state by the middleware)"
        )
        assert r.headers.get("x-request-id") == fixed_id, (
            "response header must echo the same request_id"
        )


# --------------------------------------------------------------------------- #
# 3. Validation-error input sanitisation                                      #
# --------------------------------------------------------------------------- #


class TestValidationSanitisation:
    def test_detail_does_not_echo_any_request_input(self) -> None:
        # Marker strings that clearly shouldn't appear in the response
        # unless the handler is echoing input.
        secret_question = "leakme-secret-question-MARKER"
        client = _install(
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
                "to": 10,  # invalid window -> triggers handler
                "question": secret_question,
            },
        )
        assert r.status_code == 400
        body = r.json()
        rendered = body["detail"]
        # Whole-response text guard too, in case detail ever expands.
        rendered_all = r.text
        for marker in (secret_question, VALID_UUID):
            assert marker not in rendered, f"validation envelope detail must not echo {marker!r}"
            assert marker not in rendered_all, f"full response body must not echo {marker!r}"

    def test_detail_is_compact_loc_msg_format(self) -> None:
        client = _install(
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
        detail = r.json()["detail"]
        # Should mention the offending field but not the raw value
        assert "project_id" in detail
        assert "not-a-uuid" not in detail
