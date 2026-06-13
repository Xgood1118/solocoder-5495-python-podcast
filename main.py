import os
import signal
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.config import get_settings
from app.database import init_db, get_db
from app.api import api_router
from app.api.feeds import router as feeds_router
from app.utils.logging import logger, setup_logger

settings = get_settings()

setup_logger(
    name="podcast",
    log_file="./logs/app.log",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION}...")
    logger.info(f"Debug mode: {settings.DEBUG}")
    logger.info(f"Storage directory: {settings.STORAGE_DIR}")

    try:
        init_db()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"Database initialization failed: {e}", exc_info=True)

    try:
        from app.services.audio_service import AudioService
        audio_svc = AudioService()
        audio_svc._check_ffmpeg()
        logger.info("FFmpeg check passed")
    except Exception as e:
        logger.warning(f"FFmpeg check warning: {e}")

    yield

    logger.info("Shutting down gracefully...")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Company Internal Content Auto-Podcast Generation Backend",
    lifespan=lifespan,
    debug=settings.DEBUG,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_logger(request: Request, call_next):
    start_time = time.time()
    request_id = request.headers.get("X-Request-ID", str(hash(request.url.path + str(time.time()))))

    logger.info(
        f"Request started | {request_id} | {request.method} {request.url.path} | "
        f"IP: {request.client.host if request.client else 'unknown'}"
    )

    try:
        response = await call_next(request)
        process_time = (time.time() - start_time) * 1000
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-MS"] = str(process_time)

        logger.info(
            f"Request completed | {request_id} | {request.method} {request.url.path} | "
            f"Status: {response.status_code} | Duration: {process_time:.2f}ms"
        )

        return response
    except Exception as e:
        process_time = (time.time() - start_time) * 1000
        logger.error(
            f"Request failed | {request_id} | {request.method} {request.url.path} | "
            f"Error: {str(e)} | Duration: {process_time:.2f}ms",
            exc_info=True,
        )
        raise


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning(f"Validation error: {exc.errors()}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Validation failed",
            "errors": exc.errors(),
        },
    )


@app.exception_handler(ValidationError)
async def pydantic_validation_handler(request: Request, exc: ValidationError):
    logger.warning(f"Pydantic validation error: {exc.errors()}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": "Data validation failed",
            "errors": exc.errors(),
        },
    )


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"detail": f"Resource not found: {request.url.path}"},
    )


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    logger.error(f"Internal server error: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error",
            "error": str(exc) if settings.DEBUG else None,
        },
    )


app.include_router(api_router)

app.include_router(
    feeds_router,
    prefix="/api/v1",
    tags=["feeds"],
)

app.include_router(
    feeds_router,
    prefix="",
    include_in_schema=False,
)


@app.get("/", include_in_schema=False)
async def root():
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "health": "/api/v1/health",
        "timestamp": datetime.utcnow().isoformat(),
    }


def handle_shutdown(signum, frame):
    logger.info(f"Received signal {signum}, shutting down...")
    sys.exit(0)


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level="info",
        access_log=False,
    )
