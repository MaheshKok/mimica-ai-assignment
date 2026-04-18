"""Runtime configuration for the Enriched QA Service.

``Settings`` is a :class:`~pydantic_settings.BaseSettings` subclass that
reads environment variables with defaults matching ``architect.md``
section 11. Using ``pydantic-settings`` from Phase 2 avoids a two-step
migration later.
"""

from __future__ import annotations

from pydantic import Field, PositiveInt
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Attributes:
        workflow_api_url: Base URL for the Workflow Services API.
        storage_base_url: Base URL for the S3-compatible storage service.
        storage_bucket: Logical bucket name (used only in log/span attrs).
        max_concurrent_fetches: Per-request cap on in-flight storage fetches.
        global_fetch_concurrency: Process-wide cap on in-flight storage
            fetches across all requests.
        max_relevant_images: ``top_k`` passed to the relevance ranker.
        max_rank_input: Cap on ranker input size. Oversampled refs are
            downsampled uniformly over the request time window.
        filter_workers: Size of the ranker ``ProcessPoolExecutor``. ``None``
            means ``os.cpu_count()``.
        max_fetch_failure_ratio: Fraction of storage fetches that may fail
            before the orchestrator raises
            :class:`~app.core.errors.PartialFailureThresholdExceeded`.
        assume_sorted_stream: When true, the orchestrator short-circuits
            streaming once it sees ``timestamp >= to``.
        request_timeout_ms: Total per-request budget before 504.
        otel_exporter_otlp_endpoint: OTLP collector endpoint. ``None``
            means use the console exporter.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    workflow_api_url: str = Field(default="http://localhost:9000")
    storage_base_url: str = Field(default="http://localhost:9100")
    storage_bucket: str = Field(default="mimica-screenshots")

    max_concurrent_fetches: PositiveInt = Field(default=25)
    global_fetch_concurrency: PositiveInt = Field(default=100)

    max_relevant_images: PositiveInt = Field(default=20)
    max_rank_input: PositiveInt = Field(default=500)
    filter_workers: int | None = Field(default=None)

    max_fetch_failure_ratio: float = Field(default=0.2, ge=0.0, le=1.0)
    assume_sorted_stream: bool = Field(default=True)

    request_timeout_ms: PositiveInt = Field(default=15000)

    otel_exporter_otlp_endpoint: str | None = Field(default=None)
