import asyncio
import hashlib
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar
from functools import wraps

from app.utils.logging import logger

T = TypeVar("T")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_ts() -> int:
    return int(time.time())


def gen_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:16]}"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def safe_filename(name: str, max_len: int = 100) -> str:
    name = re.sub(r"[^\w\u4e00-\u9fff.\-]", "_", name.strip())
    if len(name) > max_len:
        name = name[:max_len]
    return name or "unnamed"


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def async_retry(max_retries: int = 3, delay: float = 2.0, backoff: float = 2.0, exceptions: tuple = (Exception,)):
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            attempt = 0
            while True:
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    attempt += 1
                    if attempt >= max_retries:
                        raise
                    wait = delay * (backoff ** (attempt - 1))
                    logger.warning(f"Retry {attempt}/{max_retries} for {func.__name__} after {wait}s: {e}")
                    await asyncio.sleep(wait)
        return wrapper
    return decorator


def sync_retry(max_retries: int = 3, delay: float = 2.0, backoff: float = 2.0, exceptions: tuple = (Exception,)):
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    attempt += 1
                    if attempt >= max_retries:
                        raise
                    wait = delay * (backoff ** (attempt - 1))
                    logger.warning(f"Retry {attempt}/{max_retries} for {func.__name__} after {wait}s: {e}")
                    time.sleep(wait)
        return wrapper
    return decorator


def read_json(path: str | Path, default: Optional[T] = None) -> T | dict | list:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def write_json(path: str | Path, data: Any) -> None:
    ensure_dir(Path(path).parent)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def format_duration(seconds: float) -> str:
    if seconds <= 0:
        return "00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def chunk_text(text: str, max_chars: int = 3000) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    sentences = re.split(r"(?<=[。！？.!?\n])", text)
    current = ""
    for s in sentences:
        if len(current) + len(s) <= max_chars:
            current += s
        else:
            if current:
                chunks.append(current.strip())
            current = s
    if current.strip():
        chunks.append(current.strip())
    return chunks


def clean_text(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
