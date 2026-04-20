"""Mock S3-compatible storage API.

Standalone FastAPI app that returns deterministic image bytes keyed by
``image_id``. Swappable with a real S3/GCS backend because the storage
adapter only depends on the ``ScreenshotStorageClient`` port.
"""
