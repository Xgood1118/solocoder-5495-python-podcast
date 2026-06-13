from fastapi import APIRouter

from app.api.podcasts import router as podcasts_router
from app.api.feeds import router as feeds_router
from app.api.stats import router as stats_router
from app.api.distribution import router as distribution_router
from app.api.voices import router as voices_router
from app.api.health import router as health_router

api_router = APIRouter(prefix="/api/v1")

api_router.include_router(podcasts_router)
api_router.include_router(feeds_router)
api_router.include_router(stats_router)
api_router.include_router(distribution_router)
api_router.include_router(voices_router)
api_router.include_router(health_router)

__all__ = ["api_router"]
