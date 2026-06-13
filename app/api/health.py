from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from datetime import datetime

from app.database import get_db
from app.config import get_settings
from app.utils.logging import logger

settings = get_settings()

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(
    db: Session = Depends(get_db),
):
    try:
        db.execute("SELECT 1")
        db_ok = True
    except Exception as e:
        logger.warning(f"Database health check failed: {e}")
        db_ok = False

    return {
        "status": "healthy" if db_ok else "degraded",
        "version": settings.APP_VERSION,
        "app_name": settings.APP_NAME,
        "database": "ok" if db_ok else "error",
        "timestamp": datetime.utcnow().isoformat(),
        "debug": settings.DEBUG,
    }


@router.get("/info")
async def app_info():
    return {
        "app_name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "port": settings.PORT,
        "host": settings.HOST,
        "default_language": settings.WHISPER_LANGUAGE,
        "default_voice_a": settings.DEFAULT_VOICE_A,
        "default_voice_b": settings.DEFAULT_VOICE_B,
        "audio_format": settings.AUDIO_OUTPUT_FORMAT,
        "audio_bitrate": settings.AUDIO_BITRATE,
        "storage_dir": settings.STORAGE_DIR,
    }
