"""Mock S3-compatible storage service.

Minimal FastAPI app that serves deterministic bytes for any ``image_id``.
The path type specifier ``{image_id:path}`` matters: it captures the whole
remainder of the URL after ``/images/`` so the real storage adapter can
safely URL-encode identifiers containing ``/`` (percent-encoded to ``%2F``)
and the mock still routes them correctly after Starlette's decode.

To drive partial-failure tests deterministically without flaky network
behaviour, any ``image_id`` starting with ``missing-`` returns 404.
Everything else returns ``f"fake-image::{image_id}".encode()`` verbatim,
so integration tests can assert the round-tripped bytes match the
original (including the pre-encoding) identifier.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response


def create_app() -> FastAPI:
    """Build the storage mock FastAPI app.

    Returns:
        A configured :class:`FastAPI` instance exposing the single route
        ``GET /images/{image_id:path}``.
    """
    application = FastAPI(title="Mock Storage API", docs_url=None, redoc_url=None)

    @application.get("/images/{image_id:path}")
    async def get_image(image_id: str) -> Response:
        """Return deterministic bytes for ``image_id`` or 404 for ``missing-*``."""
        if image_id.startswith("missing-"):
            raise HTTPException(status_code=404, detail=f"not found: {image_id}")
        return Response(
            content=f"fake-image::{image_id}".encode(),
            media_type="application/octet-stream",
        )

    return application


app = create_app()
