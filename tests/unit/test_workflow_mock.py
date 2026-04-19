"""Unit tests for the workflow mock FastAPI app.

Tests the mock's own behaviour (NDJSON shape, shuffle determinism, QA
echo semantics). End-to-end tests via the real adapter live in the
integration suite.
"""

from __future__ import annotations

import json
from uuid import uuid4

import httpx

from mock_services.workflow_api.app import DEFAULT_REFS, app, create_app


async def _client_for(fastapi_app) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fastapi_app),
        base_url="http://workflow.mock",
    )


async def test_stream_returns_default_refs_in_order() -> None:
    async with (
        await _client_for(app) as client,
        client.stream("GET", f"/projects/{uuid4()}/stream") as r,
    ):
        assert r.status_code == 200
        rows = [json.loads(line) async for line in r.aiter_lines() if line]
    assert rows == list(DEFAULT_REFS)


async def test_shuffle_flag_permutes_deterministically_per_project() -> None:
    # Same project_id + shuffle=true must produce the same permutation.
    pid = uuid4()
    async with await _client_for(app) as client:
        async with client.stream("GET", f"/projects/{pid}/stream?shuffle=true") as r1:
            first = [json.loads(line) async for line in r1.aiter_lines() if line]
        async with client.stream("GET", f"/projects/{pid}/stream?shuffle=true") as r2:
            second = [json.loads(line) async for line in r2.aiter_lines() if line]
    assert first == second
    # And it should actually have been shuffled (default refs are sorted).
    assert first != list(DEFAULT_REFS)


async def test_factory_accepts_custom_refs() -> None:
    custom = create_app(
        refs=[
            {"timestamp": 10, "screenshot_url": "x.png"},
            {"timestamp": 20, "screenshot_url": "y.png"},
        ]
    )
    async with (
        await _client_for(custom) as client,
        client.stream("GET", f"/projects/{uuid4()}/stream") as r,
    ):
        rows = [json.loads(line) async for line in r.aiter_lines() if line]
    assert [row["screenshot_url"] for row in rows] == ["x.png", "y.png"]


async def test_qa_answer_echoes_question_and_ids_in_order() -> None:
    async with await _client_for(app) as client:
        r = await client.post(
            "/qa/answer",
            json={"question": "what?", "relevant_images": ["z", "a", "m"]},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["answer"] == "Q: what? | IDs: z,a,m", (
        "QA mock must preserve the image id order it received"
    )


async def test_qa_answer_rejects_invalid_body() -> None:
    async with await _client_for(app) as client:
        r = await client.post("/qa/answer", json={"question": ""})
    assert r.status_code == 422
