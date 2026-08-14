"""API routers."""

from marriage_ocr_api.api.routers.health import router as health_router
from marriage_ocr_api.api.routers.jobs import router as jobs_router
from marriage_ocr_api.batches.routers import router as batches_router
from marriage_ocr_api.exports.routers import router as exports_router
from marriage_ocr_api.records.routers import router as records_router

__all__ = ["batches_router", "exports_router", "health_router", "jobs_router", "records_router"]
