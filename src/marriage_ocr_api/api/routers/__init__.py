"""API routers."""

from marriage_ocr_api.api.routers.health import router as health_router
from marriage_ocr_api.api.routers.jobs import router as jobs_router
from marriage_ocr_api.records.routers import router as records_router

__all__ = ["health_router", "jobs_router", "records_router"]
