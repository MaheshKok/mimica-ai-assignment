"""In-memory fake implementation of the ``ScreenshotStorageClient`` port.

Used by unit tests and as the Phase 3 default dependency so ``make run``
works before real HTTP adapters exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import StorageFetchError


@dataclass
class FakeScreenshotStorage:
    """Dict-backed fake storage client.

    Returns bytes for known ids and raises
    :class:`~app.core.errors.StorageFetchError` for ids that are either
    unknown or explicitly marked as missing. ``call_count`` lets tests
    assert the orchestrator did not call storage on paths where it was
    expected to short-circuit (empty window).

    Attributes:
        images: Map from image id to byte payload.
        missing: Image ids that should always raise even if present in
            ``images``. Lets tests simulate transient failures.
        call_count: Number of times ``get_image`` has been invoked.
    """

    images: dict[str, bytes] = field(default_factory=dict)
    missing: set[str] = field(default_factory=set)
    call_count: int = 0

    async def get_image(self, image_id: str) -> bytes:
        """Return the stored bytes for ``image_id`` or raise ``StorageFetchError``.

        Args:
            image_id: The image identifier to fetch.

        Returns:
            The stored bytes.

        Raises:
            StorageFetchError: If ``image_id`` is in ``missing`` or not
                present in ``images``.
        """
        self.call_count += 1
        if image_id in self.missing or image_id not in self.images:
            raise StorageFetchError(image_id, KeyError(image_id))
        return self.images[image_id]
