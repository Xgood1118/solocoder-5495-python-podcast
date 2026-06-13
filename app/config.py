import os
from pathlib import Path
from typing import Optional
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

    PORT: int = 8000
    HOST: str = "0.0.0.0"

    DATABASE_URL: str = "sqlite:///./podcast.db"

    STORAGE_DIR: str = "./storage"
    AUDIO_DIR: str = "./storage/audio"
    SCRIPT_DIR: str = "./storage/scripts"
    RSS_DIR: str = "./storage/rss"
    SUBTITLE_DIR: str = "./storage/subtitles"

    LLM_API_BASE: str = "http://localhost:11434/v1"
    LLM_API_KEY: str = "sk-xxx"
    LLM_MODEL: str = "gpt-3.5-turbo"
    LLM_TIMEOUT: int = 120

    WHISPER_MODEL: str = "base"
    WHISPER_LANGUAGE: str = "zh"

    DEFAULT_VOICE_A: str = "zh-CN-XiaoxiaoNeural"
    DEFAULT_VOICE_B: str = "zh-CN-YunxiNeural"
    DEFAULT_SILENCE_MS: int = 800
    AUDIO_OUTPUT_FORMAT: str = "mp3"
    AUDIO_BITRATE: str = "128k"
    AUDIO_SAMPLE_RATE: int = 44100
    AUDIO_CHANNELS: int = 2

    TTS_MAX_RETRIES: int = 3
    TTS_RETRY_DELAY: float = 2.0

    FFMPEG_PATH: Optional[str] = None

    APP_NAME: str = "Podcast Generator API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    def ensure_dirs(self) -> None:
        for d in [self.STORAGE_DIR, self.AUDIO_DIR, self.SCRIPT_DIR, self.RSS_DIR, self.SUBTITLE_DIR]:
            Path(d).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
