"""Tests for the Pydantic wire schemas.

Derived from the docstrings of ``app.api.schemas``. Covers alias handling,
boundary values, validation rejection, and default behaviour. Written
against the stated contract without inspecting model implementation.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.api.schemas import EnrichedQARequest, EnrichedQAResponse, Meta

VALID_UUID = "8b80353b-aee6-4835-ba7e-c3b79010bc0b"


def _valid_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "project_id": VALID_UUID,
        "from": 1_000,
        "to": 2_000,
        "question": "what is happening?",
    }
    body.update(overrides)
    return body


class TestEnrichedQARequestAlias:
    def test_accepts_from_wire_alias(self) -> None:
        req = EnrichedQARequest.model_validate(_valid_body())
        assert req.from_ == 1_000

    def test_accepts_from_name_when_populate_by_name_is_on(self) -> None:
        body = _valid_body()
        body.pop("from")
        body["from_"] = 1_000
        req = EnrichedQARequest.model_validate(body)
        assert req.from_ == 1_000

    def test_parses_project_id_as_uuid_instance(self) -> None:
        req = EnrichedQARequest.model_validate(_valid_body())
        assert isinstance(req.project_id, UUID)
        assert str(req.project_id) == VALID_UUID


class TestEnrichedQARequestWindow:
    def test_rejects_from_equal_to(self) -> None:
        with pytest.raises(ValidationError):
            EnrichedQARequest.model_validate(_valid_body(**{"from": 10, "to": 10}))

    def test_rejects_from_greater_than_to(self) -> None:
        with pytest.raises(ValidationError):
            EnrichedQARequest.model_validate(_valid_body(**{"from": 20, "to": 10}))

    def test_accepts_from_zero(self) -> None:
        EnrichedQARequest.model_validate(_valid_body(**{"from": 0, "to": 1}))

    def test_rejects_negative_from(self) -> None:
        with pytest.raises(ValidationError):
            EnrichedQARequest.model_validate(_valid_body(**{"from": -1, "to": 10}))

    def test_rejects_negative_to(self) -> None:
        with pytest.raises(ValidationError):
            EnrichedQARequest.model_validate(_valid_body(**{"from": 0, "to": -1}))


class TestEnrichedQARequestQuestion:
    def test_rejects_empty_question(self) -> None:
        with pytest.raises(ValidationError):
            EnrichedQARequest.model_validate(_valid_body(question=""))

    def test_accepts_single_char_question(self) -> None:
        req = EnrichedQARequest.model_validate(_valid_body(question="q"))
        assert req.question == "q"

    def test_accepts_max_length_question(self) -> None:
        q = "a" * 1024
        req = EnrichedQARequest.model_validate(_valid_body(question=q))
        assert len(req.question) == 1024

    def test_rejects_question_longer_than_max(self) -> None:
        with pytest.raises(ValidationError):
            EnrichedQARequest.model_validate(_valid_body(question="a" * 1025))


class TestEnrichedQARequestProjectId:
    def test_rejects_invalid_uuid_string(self) -> None:
        with pytest.raises(ValidationError):
            EnrichedQARequest.model_validate(_valid_body(project_id="not-a-uuid"))

    def test_rejects_missing_project_id(self) -> None:
        body = _valid_body()
        body.pop("project_id")
        with pytest.raises(ValidationError):
            EnrichedQARequest.model_validate(body)

    def test_accepts_uuid_object(self) -> None:
        req = EnrichedQARequest.model_validate(_valid_body(project_id=uuid4()))
        assert isinstance(req.project_id, UUID)


class TestMeta:
    def test_default_errors_is_empty_dict(self) -> None:
        meta = Meta(request_id="r1", images_considered=0, images_relevant=0)
        assert meta.errors == {}

    def test_default_latency_is_empty_dict(self) -> None:
        meta = Meta(request_id="r1", images_considered=0, images_relevant=0)
        assert meta.latency_ms == {}

    def test_rejects_negative_images_considered(self) -> None:
        with pytest.raises(ValidationError):
            Meta(request_id="r1", images_considered=-1, images_relevant=0)

    def test_rejects_negative_images_relevant(self) -> None:
        with pytest.raises(ValidationError):
            Meta(request_id="r1", images_considered=0, images_relevant=-1)

    def test_accepts_populated_fields(self) -> None:
        meta = Meta(
            request_id="r1",
            images_considered=100,
            images_relevant=10,
            errors={"storage_fetch_failed": 2},
            latency_ms={"total": 900},
        )
        assert meta.errors == {"storage_fetch_failed": 2}
        assert meta.latency_ms == {"total": 900}


class TestEnrichedQAResponse:
    def test_wraps_answer_and_meta(self) -> None:
        meta = Meta(request_id="r1", images_considered=0, images_relevant=0)
        resp = EnrichedQAResponse(answer="yes", meta=meta)
        assert resp.answer == "yes"
        assert resp.meta is meta

    def test_accepts_empty_answer(self) -> None:
        meta = Meta(request_id="r1", images_considered=0, images_relevant=0)
        resp = EnrichedQAResponse(answer="", meta=meta)
        assert resp.answer == ""
