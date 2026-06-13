from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import VoiceInfo
from app.services.tts_service import TTSService
from app.services.audio_service import AudioService
from app.utils.logging import logger

router = APIRouter(prefix="/voices", tags=["voices"])

tts_service = TTSService()
audio_service = AudioService()


@router.get("", response_model=List[VoiceInfo])
async def list_voices(
    locale: Optional[str] = None,
    gender: Optional[str] = None,
):
    voices = tts_service.list_voices()
    if locale:
        voices = [v for v in voices if v.locale.lower().startswith(locale.lower())]
    if gender:
        voices = [v for v in voices if v.gender.lower() == gender.lower()]
    return voices


@router.post("/preview")
async def preview_voice(
    text: str,
    voice: str,
    emotion: str = "neutral",
):
    if not text or len(text.strip()) == 0:
        raise HTTPException(status_code=400, detail="Text is required")
    if len(text) > 200:
        raise HTTPException(status_code=400, detail="Preview text limited to 200 characters")

    voice_info = tts_service.get_voice_by_name(voice)
    if not voice_info:
        raise HTTPException(status_code=400, detail="Invalid voice name")

    try:
        audio_data = await tts_service.synthesize_segment_stream(text, voice, emotion)
        from fastapi.responses import Response
        return Response(
            audio_data,
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": f'inline; filename="preview.mp3"',
            },
        )
    except Exception as e:
        logger.error(f"Voice preview failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{voice_name}", response_model=VoiceInfo)
async def get_voice(voice_name: str):
    voice = tts_service.get_voice_by_name(voice_name)
    if not voice:
        raise HTTPException(status_code=404, detail="Voice not found")
    return voice


@router.get("/defaults/{language}")
async def get_default_voices(language: str):
    voice_a, voice_b = tts_service.get_default_voices(language)
    return {
        "language": language,
        "voice_a": voice_a,
        "voice_b": voice_b,
    }
