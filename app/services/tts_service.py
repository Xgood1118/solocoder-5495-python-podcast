import asyncio
from typing import List, Optional

import edge_tts

from app.config import get_settings
from app.schemas import VoiceInfo
from app.utils.helpers import async_retry, ensure_dir, gen_id, safe_filename
from app.utils.logging import logger

settings = get_settings()

AVAILABLE_VOICES = [
    VoiceInfo(name="zh-CN-XiaoxiaoNeural", display_name="晓晓（普通话）", gender="female", locale="zh-CN"),
    VoiceInfo(name="zh-CN-YunxiNeural", display_name="云希（普通话）", gender="male", locale="zh-CN"),
    VoiceInfo(name="zh-CN-YunjianNeural", display_name="云健（普通话）", gender="male", locale="zh-CN"),
    VoiceInfo(name="zh-CN-XiaoyiNeural", display_name="晓伊（普通话）", gender="female", locale="zh-CN"),
    VoiceInfo(name="zh-HK-HiuGaaiNeural", display_name="曉佳（粤语）", gender="female", locale="zh-HK"),
    VoiceInfo(name="zh-HK-WanLungNeural", display_name="雲龍（粤语）", gender="male", locale="zh-HK"),
    VoiceInfo(name="zh-TW-HsiaoChenNeural", display_name="曉臻（台语）", gender="female", locale="zh-TW"),
    VoiceInfo(name="zh-TW-YunJheNeural", display_name="雲哲（台语）", gender="male", locale="zh-TW"),
    VoiceInfo(name="en-US-AriaNeural", display_name="Aria (US)", gender="female", locale="en-US"),
    VoiceInfo(name="en-US-GuyNeural", display_name="Guy (US)", gender="male", locale="en-US"),
    VoiceInfo(name="en-US-JennyNeural", display_name="Jenny (US)", gender="female", locale="en-US"),
    VoiceInfo(name="en-GB-SoniaNeural", display_name="Sonia (UK)", gender="female", locale="en-GB"),
    VoiceInfo(name="en-GB-RyanNeural", display_name="Ryan (UK)", gender="male", locale="en-GB"),
    VoiceInfo(name="ja-JP-NanamiNeural", display_name="七海（日语）", gender="female", locale="ja-JP"),
    VoiceInfo(name="ja-JP-KeitaNeural", display_name="圭太（日语）", gender="male", locale="ja-JP"),
    VoiceInfo(name="ko-KR-SunHiNeural", display_name="선희（韩语）", gender="female", locale="ko-KR"),
    VoiceInfo(name="ko-KR-InJoonNeural", display_name="인준（韩语）", gender="male", locale="ko-KR"),
]

EMOTION_RATE_MAP = {
    "neutral": "+0%",
    "happy": "+10%",
    "excited": "+20%",
    "serious": "-5%",
    "curious": "+5%",
    "thoughtful": "-10%",
}

EMOTION_VOLUME_MAP = {
    "neutral": "+0%",
    "happy": "+5%",
    "excited": "+10%",
    "serious": "+0%",
    "curious": "+0%",
    "thoughtful": "-5%",
}


class TTSService:
    def __init__(self):
        ensure_dir(settings.AUDIO_DIR)

    @staticmethod
    def list_voices() -> List[VoiceInfo]:
        return AVAILABLE_VOICES

    @staticmethod
    def get_voice_by_name(name: str) -> Optional[VoiceInfo]:
        for v in AVAILABLE_VOICES:
            if v.name == name:
                return v
        return None

    @staticmethod
    def get_default_voices(language: str = "zh") -> tuple[str, str]:
        if language.startswith("zh"):
            return settings.DEFAULT_VOICE_A, settings.DEFAULT_VOICE_B
        elif language.startswith("en"):
            return "en-US-AriaNeural", "en-US-GuyNeural"
        elif language.startswith("ja"):
            return "ja-JP-NanamiNeural", "ja-JP-KeitaNeural"
        elif language.startswith("ko"):
            return "ko-KR-SunHiNeural", "ko-KR-InJoonNeural"
        return settings.DEFAULT_VOICE_A, settings.DEFAULT_VOICE_B

    @async_retry(
        max_retries=settings.TTS_MAX_RETRIES,
        delay=settings.TTS_RETRY_DELAY,
        exceptions=(Exception,),
    )
    async def synthesize_segment(
        self,
        text: str,
        voice: str,
        emotion: str = "neutral",
        podcast_id: Optional[str] = None,
        segment_id: Optional[str] = None,
    ) -> tuple[str, float]:
        text = text.strip()
        if not text:
            raise ValueError("Empty text for TTS synthesis")

        if not segment_id:
            segment_id = gen_id("seg_")

        safe_text = safe_filename(text[:30])
        output_path = f"{settings.AUDIO_DIR}/{podcast_id or 'temp'}_{segment_id}_{safe_text}.mp3"
        ensure_dir(output_path.rsplit("/", 1)[0])

        rate = EMOTION_RATE_MAP.get(emotion, EMOTION_RATE_MAP["neutral"])
        volume = EMOTION_VOLUME_MAP.get(emotion, EMOTION_VOLUME_MAP["neutral"])

        logger.info(
            f"TTS synthesizing: voice={voice}, emotion={emotion}, "
            f"rate={rate}, volume={volume}, text_len={len(text)}"
        )

        try:
            communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, volume=volume)
            await communicate.save(output_path)
        except Exception as e:
            logger.error(f"TTS synthesis failed for segment {segment_id}: {e}")
            raise

        duration = await self._get_audio_duration(output_path)
        logger.info(f"TTS completed: {output_path}, duration={duration:.2f}s")
        return output_path, duration

    async def synthesize_segment_stream(
        self,
        text: str,
        voice: str,
        emotion: str = "neutral",
    ) -> bytes:
        text = text.strip()
        if not text:
            raise ValueError("Empty text for TTS synthesis")

        rate = EMOTION_RATE_MAP.get(emotion, EMOTION_RATE_MAP["neutral"])
        volume = EMOTION_VOLUME_MAP.get(emotion, EMOTION_VOLUME_MAP["neutral"])

        communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, volume=volume)

        audio_chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])

        return b"".join(audio_chunks)

    @staticmethod
    async def _get_audio_duration(filepath: str) -> float:
        try:
            from mutagen.mp3 import MP3

            audio = MP3(filepath)
            return float(audio.info.length)
        except Exception as e:
            logger.warning(f"Could not get audio duration for {filepath}: {e}")
            return 0.0
