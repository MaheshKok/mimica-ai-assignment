"""Tests for the port-level exception hierarchy.

Derived from the docstrings of ``app.core.errors``. Verifies inheritance,
attribute preservation, message content, and corner cases around the
partial-failure ratio computation.
"""

from __future__ import annotations

import pytest

from app.core.errors import (
    EnrichedQAError,
    PartialFailureThresholdExceededError,
    StorageFetchError,
    WorkflowUpstreamError,
)


class TestBase:
    def test_base_is_exception(self) -> None:
        assert issubclass(EnrichedQAError, Exception)


class TestStorageFetchError:
    def test_is_enriched_qa_error(self) -> None:
        assert issubclass(StorageFetchError, EnrichedQAError)

    def test_preserves_image_id(self) -> None:
        err = StorageFetchError("img-7.png", RuntimeError("boom"))
        assert err.image_id == "img-7.png"

    def test_preserves_cause_object_identity(self) -> None:
        cause = RuntimeError("boom")
        err = StorageFetchError("x", cause)
        assert err.cause is cause

    def test_message_contains_image_id(self) -> None:
        err = StorageFetchError("img-7.png", RuntimeError("boom"))
        assert "img-7.png" in str(err)

    def test_message_contains_cause(self) -> None:
        err = StorageFetchError("img", RuntimeError("boom"))
        assert "boom" in str(err)

    def test_catchable_as_exception(self) -> None:
        with pytest.raises(Exception, match="storage fetch"):
            raise StorageFetchError("img", RuntimeError("x"))


class TestWorkflowUpstreamError:
    def test_is_enriched_qa_error(self) -> None:
        assert issubclass(WorkflowUpstreamError, EnrichedQAError)

    def test_preserves_cause(self) -> None:
        cause = TimeoutError("slow")
        err = WorkflowUpstreamError(cause)
        assert err.cause is cause

    def test_message_contains_cause(self) -> None:
        err = WorkflowUpstreamError(RuntimeError("upstream-down"))
        assert "upstream-down" in str(err)


class TestPartialFailureThresholdExceededError:
    def test_is_enriched_qa_error(self) -> None:
        assert issubclass(PartialFailureThresholdExceededError, EnrichedQAError)

    def test_preserves_counts(self) -> None:
        err = PartialFailureThresholdExceededError(failed=3, total=10)
        assert err.failed == 3
        assert err.total == 10

    def test_message_contains_both_counts(self) -> None:
        err = PartialFailureThresholdExceededError(failed=3, total=10)
        rendered = str(err)
        assert "3" in rendered
        assert "10" in rendered

    def test_zero_total_does_not_divide_by_zero(self) -> None:
        # Constructor must be safe even if a caller misuses it with total=0.
        # Raising DivisionError here would risk masking the real failure site.
        err = PartialFailureThresholdExceededError(failed=0, total=0)
        assert err.failed == 0
        assert err.total == 0

    @pytest.mark.parametrize(
        ("failed", "total"),
        [(0, 1), (1, 1), (5, 10), (100, 1000)],
    )
    def test_roundtrip_preserves_counts(self, failed: int, total: int) -> None:
        err = PartialFailureThresholdExceededError(failed=failed, total=total)
        assert err.failed == failed
        assert err.total == total
