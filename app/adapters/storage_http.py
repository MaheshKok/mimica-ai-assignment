"""HTTP adapter for the S3-compatible screenshot storage service.

Implements :class:`~app.ports.storage.ScreenshotStorageClient` on top of
``httpx.AsyncClient``. The adapter owns two disciplines the orchestrator
does not see:

- **Process-wide concurrency cap.** Every fetch acquires a shared
  :class:`asyncio.Semaphore` before hitting the network so the total
  number of concurrent storage requests across all in-flight HTTP
  requests stays bounded (``GLOBAL_FETCH_CONCURRENCY``). This sits *inside*
  the per-request semaphore the orchestrator already holds.
- **Error translation.** 4xx, 5xx, and transport failures all map to
  :class:`~app.core.errors.StorageFetchError` with the originating
  exception preserved as ``cause``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx

from app.core.errors import StorageFetchError

if TYPE_CHECKING:
    import asyncio


class HttpxScreenshotStorageClient:
    """HTTP implementation of :class:`ScreenshotStorageClient`.

    Attributes:
        _client: Shared :class:`httpx.AsyncClient` (owned by lifespan).
        _base_url: Storage base URL, without trailing slash.
        _global_sem: Process-wide concurrency cap. Acquired on every call.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        global_semaphore: asyncio.Semaphore,
    ) -> None:
        """Store the injected dependencies.

        Args:
            client: Shared async HTTP client. Ownership stays with the caller.
            base_url: Base URL of the storage service.
            global_semaphore: Process-wide storage concurrency cap.
        """
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._global_sem = global_semaphore

    async def get_image(self, image_id: str) -> bytes:
        """Fetch the image bytes for ``image_id``.

        Acquires the process-wide semaphore for the duration of the HTTP
        call so concurrent fetches across all in-flight requests stay
        bounded.

        Args:
            image_id: Stable image identifier.

        Returns:
            Raw image bytes.

        Raises:
            StorageFetchError: Wraps any 4xx/5xx response or transport
                error. ``StorageFetchError.cause`` preserves the original
                exception so higher layers can classify if needed.
        """
        # image_id is opaque upstream-derived text. It may contain ``/``,
        # ``?``, ``#`` or other characters that httpx would otherwise
        # interpret as URL syntax; percent-encode the whole thing as a
        # single path segment so the identifier round-trips verbatim.
        url = f"{self._base_url}/images/{quote(image_id, safe='')}"
        async with self._global_sem:
            try:
                response = await self._client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise StorageFetchError(image_id, exc) from exc
        return response.content
