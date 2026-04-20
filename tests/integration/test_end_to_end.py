"""End-to-end integration tests for the Enriched QA pipeline.

Wires the real FastAPI app, the real HTTP adapters, and the mock
services **all in the same process** via two layers of
:class:`httpx.ASGITransport`:

- The outer client hits ``/enriched-qa`` on the real app.
- The inner clients (inside the Ports bundle) hit the mock workflow
  and storage apps.

No sockets are opened; no uvicorn is started. Every byte the real
adapter would send over the wire still goes through httpx's request
machinery, so URL encoding, header handling, and response parsing
are genuinely exercised.

These tests are marked ``integration`` - run via ``pytest -m integration``
to select only this suite, or ``pytest`` to run everything.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, Response

from app.adapters.relevance_fake import FakeRelevanceRanker
from app.adapters.storage_http import HttpxScreenshotStorageClient
from app.adapters.workflow_http import HttpxWorkflowServicesClient
from app.config import Settings
from app.deps import Ports, get_ports, get_settings
from app.main import app
from mock_services.storage_api.app import create_app as create_storage_app
from mock_services.workflow_api.app import create_app as create_workflow_app

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _settings(**overrides: object) -> Settings:
    """Build a Settings instance with overrides, bypassing env files."""
    base: dict[str, object] = {
        "max_concurrent_fetches": 25,
        "global_fetch_concurrency": 100,
        "max_relevant_images": 20,
        "max_rank_input": 500,
        "max_fetch_failure_ratio": 0.2,
        "assume_sorted_stream": True,
        "request_timeout_ms": 15_000,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)  # type: ignore[arg-type]


async def _stack(
    workflow_app: FastAPI,
    *,
    settings: Settings | None = None,
) -> tuple[httpx.AsyncClient, httpx.AsyncClient, Ports]:
    """Construct two ASGI-backed httpx clients plus a Ports bundle.

    Uses the default storage mock and the caller-supplied workflow mock.
    Caller is responsible for closing both httpx clients.
    """
    storage_app = create_storage_app()
    wf_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=workflow_app),
        base_url="http://workflow.mock",
    )
    st_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=storage_app),
        base_url="http://storage.mock",
    )
    ports = Ports(
        workflow=HttpxWorkflowServicesClient(wf_client, "http://workflow.mock"),
        storage=HttpxScreenshotStorageClient(
            st_client,
            "http://storage.mock",
            asyncio.Semaphore(100),
        ),
        relevance=FakeRelevanceRanker(),
    )
    return wf_client, st_client, ports


async def _post(
    workflow_app: FastAPI,
    body: dict[str, object],
    *,
    settings: Settings | None = None,
) -> httpx.Response:
    """Run one full request through the real stack.

    Sets up dependency overrides, routes the POST through the real
    ``/enriched-qa`` handler, and tears down cleanly.
    """
    wf_client, st_client, ports = await _stack(workflow_app)
    app.dependency_overrides[get_ports] = lambda: ports
    app.dependency_overrides[get_settings] = lambda: settings or _settings()
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://app.test",
        ) as client:
            return await client.post("/enriched-qa", json=body)
    finally:
        app.dependency_overrides.clear()
        await wf_client.aclose()
        await st_client.aclose()


def _body(
    *,
    project_id: str | None = None,
    from_: int = 1_700_000_000,
    to: int = 1_700_001_000,
    question: str = "what is happening?",
) -> dict[str, object]:
    return {
        "project_id": project_id or str(uuid4()),
        "from": from_,
        "to": to,
        "question": question,
    }


# --------------------------------------------------------------------------- #
# Happy path                                                                  #
# --------------------------------------------------------------------------- #


class TestHappyPath:
    async def test_default_refs_round_trip_successfully(self) -> None:
        workflow_mock = create_workflow_app()
        r = await _post(workflow_mock, _body())
        assert r.status_code == 200, r.text
        data = r.json()
        # Default workflow ships 10 refs at 30s intervals starting at
        # 1_700_000_000; our 1000s window captures all of them.
        assert data["meta"]["images_considered"] == 10
        assert data["meta"]["images_relevant"] == 10
        assert data["meta"]["errors"] == {}
        # QA mock echoes the ids in the order the orchestrator sent them.
        assert data["answer"].startswith("Q: what is happening? | IDs: ")

    async def test_response_shape_matches_contract(self) -> None:
        workflow_mock = create_workflow_app()
        r = await _post(workflow_mock, _body())
        data = r.json()
        assert set(data.keys()) == {"answer", "meta"}
        assert set(data["meta"].keys()) == {
            "request_id",
            "images_considered",
            "images_relevant",
            "errors",
            "latency_ms",
        }


# --------------------------------------------------------------------------- #
# Encoded image IDs — the storage-adapter boundary                            #
# --------------------------------------------------------------------------- #


class TestEncodedImageIds:
    """Exercise the full chain with image_ids that contain URL-reserved chars.

    Verifies the end-to-end chain: the orchestrator consumes the ref,
    the real storage adapter percent-encodes the id, the storage mock
    decodes via ``{image_id:path}``, and the returned bytes make it back
    to the ranker. Any drop or mis-decode surfaces as a
    ``storage_fetch_failed`` entry in ``meta.errors`` - so a passing
    test means the entire chain handled the identifier verbatim.
    """

    async def test_ids_with_reserved_chars_round_trip(self) -> None:
        tricky = [
            {"timestamp": 1_700_000_000, "screenshot_url": "a/b.png"},
            {"timestamp": 1_700_000_030, "screenshot_url": "img.png?token=x"},
            {"timestamp": 1_700_000_060, "screenshot_url": "with space.png"},
            {"timestamp": 1_700_000_090, "screenshot_url": "a+b&c.png"},
            {"timestamp": 1_700_000_120, "screenshot_url": "图-1.png"},
            {"timestamp": 1_700_000_150, "screenshot_url": "normal.png"},
        ]
        workflow_mock = create_workflow_app(refs=tricky)
        r = await _post(workflow_mock, _body())
        assert r.status_code == 200, f"encoded-id round-trip failed - body={r.text!r}"
        data = r.json()
        assert data["meta"]["images_considered"] == 6
        assert data["meta"]["images_relevant"] == 6
        assert data["meta"]["errors"] == {}, (
            "any reserved-char id that fails round-trip would show up here"
        )
        # The ranker output is deterministic; the QA mock echoes exactly
        # the ids it received, including reserved characters. Every
        # original id must appear in the answer.
        answer = data["answer"]
        for ref in tricky:
            assert str(ref["screenshot_url"]) in answer, (
                f"image_id {ref['screenshot_url']!r} missing from answer {answer!r}"
            )


# --------------------------------------------------------------------------- #
# Partial failure                                                             #
# --------------------------------------------------------------------------- #


class TestPartialFailure:
    async def test_below_threshold_counts_missing_and_succeeds(self) -> None:
        # 10 refs, 1 flagged as ``missing-*`` -> 10% failure, below the
        # 20% default threshold. Request succeeds with meta.errors
        # populated.
        refs = [
            {"timestamp": 1_700_000_000 + i * 30, "screenshot_url": f"img-{i:03d}.png"}
            for i in range(10)
        ]
        refs[0]["screenshot_url"] = "missing-abc.png"
        workflow_mock = create_workflow_app(refs=refs)
        r = await _post(workflow_mock, _body())
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["meta"]["errors"] == {"storage_fetch_failed": 1}
        # One failed, nine succeeded
        assert data["meta"]["images_considered"] == 10
        assert data["meta"]["images_relevant"] == 9

    async def test_above_threshold_returns_502(self) -> None:
        # 10 refs, 5 missing -> 50% failure, well above 20%.
        refs = [
            {
                "timestamp": 1_700_000_000 + i * 30,
                "screenshot_url": (f"missing-{i:03d}.png" if i < 5 else f"img-{i:03d}.png"),
            }
            for i in range(10)
        ]
        workflow_mock = create_workflow_app(refs=refs)
        r = await _post(workflow_mock, _body())
        assert r.status_code == 502
        body = r.json()
        assert body["error"] == "storage_partial_failure"
        assert "5/10" in body["detail"] or "5" in body["detail"]


# --------------------------------------------------------------------------- #
# Stream fallback                                                             #
# --------------------------------------------------------------------------- #


class TestStreamSortedAssumption:
    async def test_unsorted_stream_drains_when_assume_sorted_false(self) -> None:
        # Default refs are sorted; we need to force the mock to emit
        # them shuffled AND disable the orchestrator's short-circuit.
        # The mock's ?shuffle=true query param is applied server-side,
        # but our adapter doesn't pass query params. Instead, we give
        # the mock a ref list where out-of-order refs are interleaved
        # with in-window ones, and set assume_sorted_stream=False.
        refs = [
            {"timestamp": 1_700_005_000, "screenshot_url": "past-1.png"},  # past the `to`
            {"timestamp": 1_700_000_000, "screenshot_url": "in-1.png"},
            {"timestamp": 1_700_010_000, "screenshot_url": "past-2.png"},
            {"timestamp": 1_700_000_030, "screenshot_url": "in-2.png"},
        ]
        workflow_mock = create_workflow_app(refs=refs)
        r = await _post(
            workflow_mock,
            _body(to=1_700_000_100),  # 100s window
            settings=_settings(assume_sorted_stream=False),
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["meta"]["images_considered"] == 2  # in-1, in-2


# --------------------------------------------------------------------------- #
# Order preservation                                                          #
# --------------------------------------------------------------------------- #


class TestOrderPreservation:
    async def test_ranker_order_preserved_through_qa_echo(self) -> None:
        # FakeRelevanceRanker orders by sha256 of image_id + question,
        # deterministic for fixed inputs. QA mock echoes what it
        # received. Therefore the order in the final answer must
        # reflect the ranker's deterministic order.
        refs = [{"timestamp": 1_700_000_000 + i, "screenshot_url": f"id-{i}.png"} for i in range(5)]
        workflow_mock = create_workflow_app(refs=refs)
        r1 = await _post(workflow_mock, _body(question="same-q"))
        r2 = await _post(workflow_mock, _body(question="same-q"))
        assert r1.status_code == 200
        assert r2.status_code == 200
        # Same question -> same ranker ordering -> same answer suffix.
        # (request_id differs; the `| IDs: ...` tail is deterministic.)
        tail1 = r1.json()["answer"].split("| IDs: ")[1]
        tail2 = r2.json()["answer"].split("| IDs: ")[1]
        assert tail1 == tail2


# --------------------------------------------------------------------------- #
# Cross-request concurrency                                                   #
# --------------------------------------------------------------------------- #


class TestConcurrentRequests:
    """Burst coverage — prove no cross-request leakage under load.

    Production wires ONE ``Ports`` bundle on ``app.state`` and reuses it
    for every request: the same shared ``httpx.AsyncClient``, the same
    process-wide storage semaphore, the same ``ProcessPoolExecutor``.
    Single-request tests can't catch:

    - request_id contextvar bleeding between concurrent requests,
    - the storage semaphore being accidentally scoped per-request
      instead of process-wide,
    - any other global state mutated per-request without isolation.

    These tests fire multiple requests through one shared stack via
    :func:`asyncio.gather` so the only way they all pass is if the
    wiring is genuinely concurrent-safe.
    """

    async def test_ten_concurrent_requests_all_succeed(self) -> None:
        """Ten concurrent ``/enriched-qa`` calls through one shared stack.

        Asserts all 200s, all request_ids distinct (contextvar
        isolation), and each response sees the full ten-ref mock stream
        with zero fetch failures.
        """
        workflow_mock = create_workflow_app()
        storage_app = create_storage_app()
        wf_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=workflow_mock),
            base_url="http://workflow.mock",
        )
        st_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=storage_app),
            base_url="http://storage.mock",
        )
        ports = Ports(
            workflow=HttpxWorkflowServicesClient(wf_client, "http://workflow.mock"),
            storage=HttpxScreenshotStorageClient(
                st_client, "http://storage.mock", asyncio.Semaphore(100)
            ),
            relevance=FakeRelevanceRanker(),
        )
        app.dependency_overrides[get_ports] = lambda: ports
        app.dependency_overrides[get_settings] = lambda: _settings()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://app.test",
            ) as client:
                responses = await asyncio.gather(
                    *(client.post("/enriched-qa", json=_body(question=f"q-{i}")) for i in range(10))
                )
        finally:
            app.dependency_overrides.clear()
            await wf_client.aclose()
            await st_client.aclose()

        # All succeed.
        failures = [(r.status_code, r.text) for r in responses if r.status_code != 200]
        assert not failures, f"concurrent burst had failures: {failures}"

        # request_ids must be unique — proves the middleware's contextvar
        # binding is per-request, not leaked across the gather.
        request_ids = [r.json()["meta"]["request_id"] for r in responses]
        assert len(set(request_ids)) == 10, (
            f"concurrent requests produced duplicate request_ids: {request_ids}"
        )

        # Every request saw the full mock stream with no losses.
        for r in responses:
            data = r.json()
            assert data["meta"]["images_considered"] == 10
            assert data["meta"]["images_relevant"] == 10
            assert data["meta"]["errors"] == {}

    async def test_global_semaphore_caps_across_concurrent_requests(self) -> None:
        """Shared storage semaphore caps total in-flight fetches across requests.

        Five concurrent requests, each needing ten fetches, would drive
        up to fifty simultaneous storage calls without a cap. With a
        shared semaphore of three, peak observed concurrency at the
        instrumented storage handler must never exceed three. Catches
        any regression where the semaphore is scoped per-orchestrator
        or per-request instead of process-wide.
        """
        tracker = {"count": 0, "peak": 0}
        instrumented = FastAPI()

        @instrumented.get("/images/{image_id:path}")
        async def _instrumented_get(image_id: str) -> Response:
            tracker["count"] += 1
            tracker["peak"] = max(tracker["peak"], tracker["count"])
            try:
                # Yield control so concurrent handlers pile up at the
                # semaphore boundary and peak has a chance to climb.
                await asyncio.sleep(0.01)
                return Response(
                    content=f"fake-image::{image_id}".encode(),
                    media_type="application/octet-stream",
                )
            finally:
                tracker["count"] -= 1

        workflow_mock = create_workflow_app()
        wf_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=workflow_mock),
            base_url="http://workflow.mock",
        )
        st_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=instrumented),
            base_url="http://storage.mock",
        )
        shared_sem = asyncio.Semaphore(3)
        ports = Ports(
            workflow=HttpxWorkflowServicesClient(wf_client, "http://workflow.mock"),
            storage=HttpxScreenshotStorageClient(st_client, "http://storage.mock", shared_sem),
            relevance=FakeRelevanceRanker(),
        )
        app.dependency_overrides[get_ports] = lambda: ports
        app.dependency_overrides[get_settings] = lambda: _settings()
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://app.test",
            ) as client:
                responses = await asyncio.gather(
                    *(client.post("/enriched-qa", json=_body(question=f"q-{i}")) for i in range(5))
                )
        finally:
            app.dependency_overrides.clear()
            await wf_client.aclose()
            await st_client.aclose()

        assert all(r.status_code == 200 for r in responses), [
            r.text for r in responses if r.status_code != 200
        ]
        assert tracker["peak"] <= 3, (
            f"global semaphore failed to cap across requests: "
            f"peak={tracker['peak']} exceeded cap of 3"
        )
        # Sanity: prove the test actually exercised parallelism. If peak
        # stayed at 1 the assertion above passes trivially and proves
        # nothing; fail loud instead.
        assert tracker["peak"] >= 2, (
            f"no cross-request parallelism observed: peak={tracker['peak']}; test is degenerate"
        )
