import os
from datetime import datetime
from typing import Optional, List
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query, UploadFile, File, Form, Request
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import get_settings
from app.schemas import (
    PodcastCreateFromText,
    PodcastCreateFromUrl,
    PodcastCreateFromFile,
    PodcastCreate,
    PodcastOut,
    PodcastDetail,
    PodcastUpdate,
    AudioSegmentOut,
    SegmentRetry,
    PlayStatCreate,
    PlayStatUpdate,
    PlayStatOut,
)
from app.models import Podcast, AudioSegment
from app.services.podcast_service import PodcastService
from app.services.tts_service import TTSService
from app.services.audio_service import AudioService
from app.utils.logging import logger

settings = get_settings()

router = APIRouter(prefix="/podcasts", tags=["podcasts"])

podcast_service = PodcastService()
tts_service = TTSService()
audio_service = AudioService()


def _get_owner_id(request: Request) -> str:
    owner_id = request.headers.get("X-User-Id")
    if not owner_id:
        return "default_user"
    return owner_id


async def _process_podcast_background(db_session, podcast_id: str):
    try:
        db = next(get_db())
        podcast = db.query(Podcast).get(podcast_id)
        if podcast:
            await podcast_service.process_podcast(db, podcast)
    except Exception as e:
        logger.error(f"Background processing failed for podcast {podcast_id}: {e}")


@router.post("", response_model=PodcastOut)
async def create_podcast(
    data: PodcastCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    try:
        podcast = await podcast_service.create_podcast(db, data, owner_id)
        background_tasks.add_task(_process_podcast_background, db, podcast.id)
        return podcast
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Create podcast failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/from-text", response_model=PodcastOut)
async def create_from_text(
    data: PodcastCreateFromText,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    try:
        podcast = await podcast_service.create_podcast(db, data, owner_id)
        background_tasks.add_task(_process_podcast_background, db, podcast.id)
        return podcast
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Create podcast from text failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/from-url", response_model=PodcastOut)
async def create_from_url(
    data: PodcastCreateFromUrl,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    try:
        podcast = await podcast_service.create_podcast(db, data, owner_id)
        background_tasks.add_task(_process_podcast_background, db, podcast.id)
        return podcast
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Create podcast from URL failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/from-file", response_model=PodcastOut)
async def create_from_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(...),
    description: Optional[str] = Form(None),
    language: str = Form("zh"),
    voice_a: Optional[str] = Form(None),
    voice_b: Optional[str] = Form(None),
    silence_ms: int = Form(800),
    tags: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    try:
        from app.services.input_service import InputService

        content = await file.read()
        raw_text = InputService.extract_from_bytes(content, file.filename or "uploaded.txt")
        tags_list = tags.split(",") if tags else []

        data = PodcastCreateFromFile(
            source_type="file",
            source_filename=file.filename or "uploaded",
            raw_text=raw_text,
            title=title,
            description=description,
            language=language,
            voice_a=voice_a,
            voice_b=voice_b,
            silence_ms=silence_ms,
            tags=tags_list,
        )

        podcast = await podcast_service.create_podcast(db, data, owner_id)
        background_tasks.add_task(_process_podcast_background, db, podcast.id)
        return podcast
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Create podcast from file failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=dict)
async def list_podcasts(
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    podcasts, total = podcast_service.list_podcasts(db, owner_id, status, limit, offset)
    return {
        "items": [PodcastOut.model_validate(p) for p in podcasts],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{podcast_id}", response_model=PodcastDetail)
async def get_podcast(
    podcast_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    podcast = podcast_service.get_podcast(db, podcast_id)
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")
    if podcast.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return PodcastDetail.model_validate(podcast)


@router.patch("/{podcast_id}", response_model=PodcastOut)
async def update_podcast(
    podcast_id: str,
    data: PodcastUpdate,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    podcast = podcast_service.get_podcast(db, podcast_id)
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")
    if podcast.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Access denied")

    updated = podcast_service.update_podcast(db, podcast, data)
    return PodcastOut.model_validate(updated)


@router.delete("/{podcast_id}", status_code=204)
async def delete_podcast(
    podcast_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    podcast = podcast_service.get_podcast(db, podcast_id)
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")
    if podcast.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Access denied")

    podcast_service.delete_podcast(db, podcast)
    return None


@router.post("/{podcast_id}/reprocess")
async def reprocess_podcast(
    podcast_id: str,
    background_tasks: BackgroundTasks,
    generate_subtitles: bool = True,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    podcast = podcast_service.get_podcast(db, podcast_id)
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")
    if podcast.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Access denied")

    podcast.status = "pending"
    podcast.progress = 0
    podcast.error_message = None
    db.commit()

    background_tasks.add_task(_process_podcast_background, db, podcast.id)
    return {"status": "started", "podcast_id": podcast_id}


@router.get("/{podcast_id}/audio")
async def stream_audio(
    podcast_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    podcast = podcast_service.get_podcast(db, podcast_id)
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")
    if not podcast.audio_path or not os.path.exists(podcast.audio_path):
        raise HTTPException(status_code=404, detail="Audio not available")

    file_size = os.path.getsize(podcast.audio_path)
    range_header = request.headers.get("range")

    start = 0
    end = file_size - 1

    if range_header:
        try:
            range_str = range_header.replace("bytes=", "")
            range_start, range_end = range_str.split("-")
            start = int(range_start) if range_start else 0
            end = int(range_end) if range_end else file_size - 1
        except Exception:
            pass

    content_length = end - start + 1
    content_range = f"bytes {start}-{end}/{file_size}"

    generator = await audio_service.stream_audio(
        podcast.audio_path,
        start_bytes=start,
        end_bytes=end,
    )

    headers = {
        "Content-Range": content_range,
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
    }

    return StreamingResponse(
        generator,
        status_code=206 if range_header else 200,
        media_type=f"audio/{settings.AUDIO_OUTPUT_FORMAT}",
        headers=headers,
    )


@router.get("/{podcast_id}/download")
async def download_audio(
    podcast_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    podcast = podcast_service.get_podcast(db, podcast_id)
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")
    if podcast.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if not podcast.audio_path or not os.path.exists(podcast.audio_path):
        raise HTTPException(status_code=404, detail="Audio not available")

    filename = f"{podcast.title}.{settings.AUDIO_OUTPUT_FORMAT}"
    return FileResponse(
        podcast.audio_path,
        media_type=f"audio/{settings.AUDIO_OUTPUT_FORMAT}",
        filename=filename,
    )


@router.get("/{podcast_id}/segments", response_model=List[AudioSegmentOut])
async def list_segments(
    podcast_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    podcast = podcast_service.get_podcast(db, podcast_id)
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")
    if podcast.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Access denied")

    segments = sorted(podcast.segments, key=lambda s: s.order_index)
    return [AudioSegmentOut.model_validate(s) for s in segments]


@router.post("/{podcast_id}/segments/{segment_id}/retry", response_model=AudioSegmentOut)
async def retry_segment(
    podcast_id: str,
    segment_id: str,
    retry_data: SegmentRetry,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    podcast = podcast_service.get_podcast(db, podcast_id)
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")
    if podcast.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Access denied")

    segment = db.query(AudioSegment).get(segment_id)
    if not segment or segment.podcast_id != podcast_id:
        raise HTTPException(status_code=404, detail="Segment not found")

    try:
        updated = await podcast_service.retry_segment(db, segment, retry_data)
        return AudioSegmentOut.model_validate(updated)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{podcast_id}/segments/{segment_id}/audio")
async def stream_segment_audio(
    podcast_id: str,
    segment_id: str,
    db: Session = Depends(get_db),
):
    segment = db.query(AudioSegment).get(segment_id)
    if not segment or segment.podcast_id != podcast_id:
        raise HTTPException(status_code=404, detail="Segment not found")
    if not segment.audio_path or not os.path.exists(segment.audio_path):
        raise HTTPException(status_code=404, detail="Audio not available")

    generator = await audio_service.stream_audio(segment.audio_path)
    return StreamingResponse(
        generator,
        media_type=f"audio/{settings.AUDIO_OUTPUT_FORMAT}"
    )


@router.get("/{podcast_id}/subtitle")
async def get_subtitle(
    podcast_id: str,
    format: str = "srt",
    language: str = "zh",
    db: Session = Depends(get_db),
):
    podcast = podcast_service.get_podcast(db, podcast_id)
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")

    subtitle_base = Path(settings.SUBTITLE_DIR)
    subtitle_path = subtitle_base / f"{podcast_id}_{language}.{format}"

    if not subtitle_path.exists():
        subtitle_path = subtitle_base / f"{podcast_id}_{settings.WHISPER_LANGUAGE}.{format}"

    if not subtitle_path.exists():
        raise HTTPException(status_code=404, detail="Subtitle not available")

    media_type = "application/x-subrip" if format == "srt" else "text/vtt"
    return FileResponse(
        str(subtitle_path),
        media_type=media_type,
        filename=f"{podcast_id}_{language}.{format}"
    )


@router.post("/{podcast_id}/play/start", response_model=PlayStatOut)
async def start_playback(
    podcast_id: str,
    data: PlayStatCreate,
    db: Session = Depends(get_db),
):
    try:
        stat = podcast_service.start_playback(db, podcast_id, data.user_id, data.session_id)
        return PlayStatOut.model_validate(stat)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/{podcast_id}/play/{stat_id}", response_model=PlayStatOut)
async def update_playback(
    podcast_id: str,
    stat_id: str,
    data: PlayStatUpdate,
    db: Session = Depends(get_db),
):
    from app.services.stats_service import StatsService

    stats_service = StatsService()
    stat = stats_service.record_play_event(
        db,
        stat_id,
        data.played_seconds or 0.0,
        data.last_position or 0.0,
        data.is_complete or False,
        data.stay_points,
        data.ended or False,
    )
    if not stat:
        raise HTTPException(status_code=404, detail="Play stat not found")
    return PlayStatOut.model_validate(stat)


@router.get("/{podcast_id}/script")
async def get_script(
    podcast_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    podcast = podcast_service.get_podcast(db, podcast_id)
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")
    if podcast.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if not podcast.script_path or not os.path.exists(podcast.script_path):
        raise HTTPException(status_code=404, detail="Script not available")

    from app.utils.helpers import read_json
    script_data = read_json(podcast.script_path)
    return script_data
