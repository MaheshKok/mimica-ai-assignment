"""Enriched QA Service application package.

Top-level package for the FastAPI service. Sub-packages:
    api          - HTTP route handlers and Pydantic schemas.
    core         - Domain models and orchestration logic.
    ports        - Protocol definitions for swappable dependencies.
    adapters     - Concrete implementations of the ports.
    observability - Structured logging, tracing, and request-ID middleware.
"""

__version__ = "0.1.0"
