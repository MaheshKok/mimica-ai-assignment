"""Unit tests for the storage mock FastAPI app.

Exercises the mock via an in-process ``httpx`` ASGI transport so the
tests hit every routing and path-decoding line without spinning up
uvicorn. Keep these narrow: end-to-end behaviour (storage adapter →
mock → round-trip) belongs in integration tests.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from mock_services.storage_api.app import app


async def _get(path: str) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://storage.mock",
    ) as client:
        return await client.get(path)


async def test_known_image_returns_fake_bytes() -> None:
    r = await _get("/images/img-001.png")
    assert r.status_code == 200
    assert r.content == b"fake-image::img-001.png"
    assert r.headers["content-type"] == "application/octet-stream"


async def test_missing_prefix_returns_404() -> None:
    r = await _get("/images/missing-anything.png")
    assert r.status_code == 404


async def test_slash_in_id_round_trips_via_path_decoding() -> None:
    # `/` in image_id is sent encoded as %2F; the mock's {image_id:path}
    # route must decode it back to the original string.
    encoded = quote("a/b.png", safe="")
    r = await _get(f"/images/{encoded}")
    assert r.status_code == 200
    assert r.content == b"fake-image::a/b.png"


async def test_question_mark_in_id_round_trips() -> None:
    encoded = quote("img.png?token=x", safe="")
    r = await _get(f"/images/{encoded}")
    assert r.status_code == 200
    assert r.content == b"fake-image::img.png?token=x"


async def test_unicode_id_round_trips() -> None:
    encoded = quote("图-1.png", safe="")
    r = await _get(f"/images/{encoded}")
    assert r.status_code == 200
    assert r.content == "fake-image::图-1.png".encode()
