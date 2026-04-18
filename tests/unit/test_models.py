"""Tests for the domain value objects.

Derived from the docstring and signature of ``app.core.models``. The types
are promised to be frozen, hashable, and to expose the declared attributes.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.core.models import ScreenshotRef, ScreenshotWithBytes


class TestScreenshotRef:
    def test_exposes_timestamp_and_image_id(self) -> None:
        ref = ScreenshotRef(timestamp=1_700_000_000, image_id="img-1.png")
        assert ref.timestamp == 1_700_000_000
        assert ref.image_id == "img-1.png"

    def test_is_frozen_reassigning_timestamp_raises(self) -> None:
        ref = ScreenshotRef(timestamp=1, image_id="a")
        with pytest.raises(FrozenInstanceError):
            ref.timestamp = 2  # type: ignore[misc]

    def test_is_frozen_reassigning_image_id_raises(self) -> None:
        ref = ScreenshotRef(timestamp=1, image_id="a")
        with pytest.raises(FrozenInstanceError):
            ref.image_id = "b"  # type: ignore[misc]

    def test_equal_when_fields_equal(self) -> None:
        a = ScreenshotRef(timestamp=10, image_id="x")
        b = ScreenshotRef(timestamp=10, image_id="x")
        assert a == b

    def test_not_equal_when_timestamp_differs(self) -> None:
        a = ScreenshotRef(timestamp=10, image_id="x")
        b = ScreenshotRef(timestamp=11, image_id="x")
        assert a != b

    def test_not_equal_when_image_id_differs(self) -> None:
        a = ScreenshotRef(timestamp=10, image_id="x")
        b = ScreenshotRef(timestamp=10, image_id="y")
        assert a != b

    def test_hashable_in_set(self) -> None:
        a = ScreenshotRef(timestamp=1, image_id="x")
        b = ScreenshotRef(timestamp=1, image_id="x")
        assert {a, b} == {a}

    def test_accepts_zero_timestamp(self) -> None:
        ScreenshotRef(timestamp=0, image_id="x")

    def test_accepts_unicode_image_id(self) -> None:
        ScreenshotRef(timestamp=1, image_id="图像-1.png")


class TestScreenshotWithBytes:
    def test_exposes_ref_and_data(self) -> None:
        ref = ScreenshotRef(timestamp=1, image_id="a")
        payload = b"hello"
        item = ScreenshotWithBytes(ref=ref, data=payload)
        assert item.ref is ref
        assert item.data == payload

    def test_is_frozen(self) -> None:
        ref = ScreenshotRef(timestamp=1, image_id="a")
        item = ScreenshotWithBytes(ref=ref, data=b"x")
        with pytest.raises(FrozenInstanceError):
            item.data = b"y"  # type: ignore[misc]

    def test_accepts_empty_bytes(self) -> None:
        ref = ScreenshotRef(timestamp=1, image_id="a")
        item = ScreenshotWithBytes(ref=ref, data=b"")
        assert item.data == b""

    def test_equal_when_fields_equal(self) -> None:
        ref = ScreenshotRef(timestamp=1, image_id="a")
        assert ScreenshotWithBytes(ref=ref, data=b"x") == ScreenshotWithBytes(ref=ref, data=b"x")

    def test_not_equal_when_bytes_differ(self) -> None:
        ref = ScreenshotRef(timestamp=1, image_id="a")
        assert ScreenshotWithBytes(ref=ref, data=b"x") != ScreenshotWithBytes(ref=ref, data=b"y")
