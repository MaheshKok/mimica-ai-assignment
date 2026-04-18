"""Adapter tests for :class:`~app.adapters.storage_http.HttpxScreenshotStorageClient`.

Verifies the two disciplines the adapter owns: error translation (any
4xx/5xx/transport failure becomes :class:`StorageFetchError`) and
process-wide concurrency capping via the injected semaphore.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx
import pytest

from app.adapters.storage_http import HttpxScreenshotStorageClient
from app.core.errors import StorageFetchError

if TYPE_CHECKING:
    from collections.abc import Callable


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://storage.test",
    )


# --------------------------------------------------------------------------- #
# get_image                                                                   #
# --------------------------------------------------------------------------- #


class TestGetImage:
    async def test_returns_bytes_on_200(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"image-payload")

        client = _client(handler)
        sem = asyncio.Semaphore(100)
        try:
            adapter = HttpxScreenshotStorageClient(
                client, base_url="http://storage.test", global_semaphore=sem
            )
            assert await adapter.get_image("img-a") == b"image-payload"
        finally:
            await client.aclose()

    async def test_raises_storage_fetch_on_404(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        client = _client(handler)
        sem = asyncio.Semaphore(100)
        try:
            adapter = HttpxScreenshotStorageClient(
                client, base_url="http://storage.test", global_semaphore=sem
            )
            with pytest.raises(StorageFetchError) as excinfo:
                await adapter.get_image("missing-1")
            assert excinfo.value.image_id == "missing-1"
        finally:
            await client.aclose()

    async def test_raises_storage_fetch_on_5xx(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        client = _client(handler)
        sem = asyncio.Semaphore(100)
        try:
            adapter = HttpxScreenshotStorageClient(
                client, base_url="http://storage.test", global_semaphore=sem
            )
            with pytest.raises(StorageFetchError) as excinfo:
                await adapter.get_image("a")
            assert excinfo.value.image_id == "a"
            assert excinfo.value.cause is not None
        finally:
            await client.aclose()

    async def test_raises_storage_fetch_on_timeout(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("upstream slow")

        client = _client(handler)
        sem = asyncio.Semaphore(100)
        try:
            adapter = HttpxScreenshotStorageClient(
                client, base_url="http://storage.test", global_semaphore=sem
            )
            with pytest.raises(StorageFetchError):
                await adapter.get_image("a")
        finally:
            await client.aclose()

    async def test_raises_storage_fetch_on_connect_error(self) -> None:
        async def handler(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host")

        client = _client(handler)
        sem = asyncio.Semaphore(100)
        try:
            adapter = HttpxScreenshotStorageClient(
                client, base_url="http://storage.test", global_semaphore=sem
            )
            with pytest.raises(StorageFetchError):
                await adapter.get_image("a")
        finally:
            await client.aclose()

    async def test_url_pattern_includes_image_id(self) -> None:
        captured: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.append(str(request.url))
            return httpx.Response(200, content=b"x")

        client = _client(handler)
        sem = asyncio.Semaphore(100)
        try:
            adapter = HttpxScreenshotStorageClient(
                client, base_url="http://storage.test", global_semaphore=sem
            )
            await adapter.get_image("my-image.png")
            assert captured == ["http://storage.test/images/my-image.png"]
        finally:
            await client.aclose()


# --------------------------------------------------------------------------- #
# Global semaphore                                                            #
# --------------------------------------------------------------------------- #


class TestGlobalSemaphore:
    async def test_semaphore_caps_peak_concurrent_fetches(self) -> None:
        """Many concurrent fetches must not exceed the global semaphore cap.

        Without the cap, 20 concurrent ``get_image`` calls would all hit the
        mock transport at once. The semaphore is sized to 3; peak in-flight
        must be <= 3 throughout.
        """
        peak = 0
        in_flight = 0

        async def handler(_: httpx.Request) -> httpx.Response:
            nonlocal peak, in_flight
            in_flight += 1
            peak = max(peak, in_flight)
            # yield so other coroutines get a chance to enter if the
            # semaphore is misconfigured.
            await asyncio.sleep(0)
            in_flight -= 1
            return httpx.Response(200, content=b"x")

        client = _client(handler)
        sem = asyncio.Semaphore(3)
        try:
            adapter = HttpxScreenshotStorageClient(
                client, base_url="http://storage.test", global_semaphore=sem
            )
            await asyncio.gather(*(adapter.get_image(f"img-{i}") for i in range(20)))
        finally:
            await client.aclose()
        assert peak <= 3, f"peak concurrency {peak} exceeded semaphore cap of 3"
        # Sanity check that the test actually exercised parallelism at all.
        assert peak >= 2

    async def test_semaphore_released_on_exception(self) -> None:
        """A 500 response must still release the semaphore slot."""
        fail_next = {"count": 0}

        async def handler(_: httpx.Request) -> httpx.Response:
            fail_next["count"] += 1
            if fail_next["count"] <= 2:
                return httpx.Response(500)
            return httpx.Response(200, content=b"ok")

        client = _client(handler)
        # Cap of 1 - if the semaphore doesn't release on error, the
        # third request would deadlock.
        sem = asyncio.Semaphore(1)
        try:
            adapter = HttpxScreenshotStorageClient(
                client, base_url="http://storage.test", global_semaphore=sem
            )
            for i in range(2):
                with pytest.raises(StorageFetchError):
                    await adapter.get_image(f"bad-{i}")
            # Third call should succeed because the semaphore was released.
            assert await adapter.get_image("ok") == b"ok"
        finally:
            await client.aclose()
