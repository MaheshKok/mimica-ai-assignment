"""Tests for ``app.adapters.storage_fake.FakeScreenshotStorage``.

Contract comes from the docstring on the fake and the
``ScreenshotStorageClient`` Protocol. Verifies call counting, error
translation, and behaviour of the ``missing`` override.
"""

from __future__ import annotations

import pytest

from app.adapters.storage_fake import FakeScreenshotStorage
from app.core.errors import StorageFetchError


class TestGetImage:
    async def test_returns_bytes_for_known_id(self) -> None:
        fake = FakeScreenshotStorage(images={"a": b"hello"})
        assert await fake.get_image("a") == b"hello"

    async def test_raises_storage_fetch_error_for_unknown_id(self) -> None:
        fake = FakeScreenshotStorage(images={"a": b"hello"})
        with pytest.raises(StorageFetchError) as excinfo:
            await fake.get_image("b")
        assert excinfo.value.image_id == "b"

    async def test_error_has_non_none_cause(self) -> None:
        fake = FakeScreenshotStorage()
        with pytest.raises(StorageFetchError) as excinfo:
            await fake.get_image("missing")
        assert excinfo.value.cause is not None

    async def test_missing_set_overrides_presence_in_images(self) -> None:
        fake = FakeScreenshotStorage(images={"a": b"x"}, missing={"a"})
        with pytest.raises(StorageFetchError) as excinfo:
            await fake.get_image("a")
        assert excinfo.value.image_id == "a"

    async def test_call_count_increments_on_success(self) -> None:
        fake = FakeScreenshotStorage(images={"a": b"x"})
        await fake.get_image("a")
        await fake.get_image("a")
        assert fake.call_count == 2

    async def test_call_count_increments_on_failure(self) -> None:
        fake = FakeScreenshotStorage()
        with pytest.raises(StorageFetchError):
            await fake.get_image("missing")
        assert fake.call_count == 1

    async def test_empty_bytes_value_is_returned(self) -> None:
        fake = FakeScreenshotStorage(images={"a": b""})
        assert await fake.get_image("a") == b""

    async def test_different_ids_are_isolated(self) -> None:
        fake = FakeScreenshotStorage(images={"a": b"1", "b": b"2"})
        assert await fake.get_image("a") == b"1"
        assert await fake.get_image("b") == b"2"
