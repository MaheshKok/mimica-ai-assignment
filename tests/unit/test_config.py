"""Tests for ``app.config.Settings``.

Covers default values, environment-variable overrides, boolean parsing
semantics, and validator boundaries. Tests patch ``os.environ`` via
``monkeypatch`` and disable .env loading to keep the harness hermetic.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip any ambient config vars so defaults are observable."""
    for name in [
        "WORKFLOW_API_URL",
        "STORAGE_BASE_URL",
        "STORAGE_BUCKET",
        "MAX_CONCURRENT_FETCHES",
        "GLOBAL_FETCH_CONCURRENCY",
        "MAX_RELEVANT_IMAGES",
        "MAX_RANK_INPUT",
        "FILTER_WORKERS",
        "MAX_FETCH_FAILURE_RATIO",
        "ASSUME_SORTED_STREAM",
        "REQUEST_TIMEOUT_MS",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
    ]:
        monkeypatch.delenv(name, raising=False)


def _make(**env: str) -> Settings:
    """Build a Settings instance without reading .env on disk."""
    return Settings(_env_file=None, **env)  # type: ignore[arg-type]


class TestDefaults:
    def test_workflow_api_url_default(self) -> None:
        assert _make().workflow_api_url == "http://localhost:9000"

    def test_storage_base_url_default(self) -> None:
        assert _make().storage_base_url == "http://localhost:9100"

    def test_storage_bucket_default(self) -> None:
        assert _make().storage_bucket == "mimica-screenshots"

    def test_max_concurrent_fetches_default(self) -> None:
        assert _make().max_concurrent_fetches == 25

    def test_global_fetch_concurrency_default(self) -> None:
        assert _make().global_fetch_concurrency == 100

    def test_max_relevant_images_default(self) -> None:
        assert _make().max_relevant_images == 20

    def test_max_rank_input_default(self) -> None:
        assert _make().max_rank_input == 500

    def test_filter_workers_defaults_to_none(self) -> None:
        assert _make().filter_workers is None

    def test_max_fetch_failure_ratio_default(self) -> None:
        assert _make().max_fetch_failure_ratio == pytest.approx(0.2)

    def test_assume_sorted_stream_default(self) -> None:
        assert _make().assume_sorted_stream is True

    def test_request_timeout_ms_default(self) -> None:
        assert _make().request_timeout_ms == 15_000

    def test_otel_endpoint_default_is_none(self) -> None:
        assert _make().otel_exporter_otlp_endpoint is None


class TestEnvOverrides:
    def test_env_overrides_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("WORKFLOW_API_URL", "https://prod.example/api")
        assert _make().workflow_api_url == "https://prod.example/api"

    def test_env_overrides_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAX_CONCURRENT_FETCHES", "7")
        assert _make().max_concurrent_fetches == 7

    def test_env_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("max_concurrent_fetches", "11")
        assert _make().max_concurrent_fetches == 11

    @pytest.mark.parametrize("value", ["true", "True", "TRUE", "1", "yes"])
    def test_assume_sorted_stream_truthy(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("ASSUME_SORTED_STREAM", value)
        assert _make().assume_sorted_stream is True

    @pytest.mark.parametrize("value", ["false", "False", "0", "no"])
    def test_assume_sorted_stream_falsy(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv("ASSUME_SORTED_STREAM", value)
        assert _make().assume_sorted_stream is False


class TestValidators:
    def test_positive_int_rejects_zero(self) -> None:
        with pytest.raises(ValidationError):
            _make(max_concurrent_fetches=0)  # type: ignore[arg-type]

    def test_positive_int_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            _make(max_concurrent_fetches=-5)  # type: ignore[arg-type]

    def test_ratio_accepts_zero(self) -> None:
        assert _make(max_fetch_failure_ratio=0.0).max_fetch_failure_ratio == 0.0  # type: ignore[arg-type]

    def test_ratio_accepts_one(self) -> None:
        assert _make(max_fetch_failure_ratio=1.0).max_fetch_failure_ratio == 1.0  # type: ignore[arg-type]

    def test_ratio_rejects_above_one(self) -> None:
        with pytest.raises(ValidationError):
            _make(max_fetch_failure_ratio=1.1)  # type: ignore[arg-type]

    def test_ratio_rejects_negative(self) -> None:
        with pytest.raises(ValidationError):
            _make(max_fetch_failure_ratio=-0.1)  # type: ignore[arg-type]
