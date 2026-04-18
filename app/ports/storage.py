"""Storage client port.

The :class:`ScreenshotStorageClient` Protocol defines the minimal surface
the orchestrator depends on for image retrieval. Concrete implementations
live under ``app.adapters``; tests inject fakes that satisfy this Protocol.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ScreenshotStorageClient(Protocol):
    """Port for fetching image bytes from a screenshot store.

    Any 4xx, 5xx, or transport error is translated by the implementation
    into :class:`~app.core.errors.StorageFetchError`. Callers do not need
    to (and should not try to) interpret the response otherwise.
    """

    async def get_image(self, image_id: str) -> bytes:
        """Fetch the raw bytes for a single image by id.

        Args:
            image_id: The stable image identifier.

        Returns:
            The raw image bytes.

        Raises:
            StorageFetchError: When the image cannot be retrieved for any
                reason (missing, transport failure, non-2xx response).
        """
        ...
