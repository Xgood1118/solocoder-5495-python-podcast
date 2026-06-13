import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from app.config import get_settings
from app.models import Podcast, AudioSegment, User, PlayStat
from app.schemas import (
    PodcastCreate,
    PodcastCreateFromText,
    PodcastCreateFromUrl,
    PodcastCreateFromFile,
    PodcastScript,
    ScriptSegment,
    SegmentRetry,
    PodcastUpdate,
)
from app.services.input_service import InputService
from app.services.llm_service import LLMService
from app.services.tts_service import TTSService
from app.services.audio_service import AudioService
from app.services.subtitle_service import SubtitleService
from app.utils.helpers import gen_id, safe_filename, ensure_dir, write_json
from app.utils.logging import logger

settings = get_settings()


class PodcastService:
    def __init__(self):
        self.input_service = InputService()
        self.llm_service = LLMService()
        self.tts_service = TTSService()
        self.audio_service = AudioService()
        self.subtitle_service = SubtitleService()
        ensure_dir(settings.SCRIPT_DIR)

    async def create_podcast(
        self,
        db_session,
        data: PodcastCreate,
        owner_id: str,
    ) -> Podcast:
        podcast_id = gen_id("pod_")

        raw_text = ""
        source_url = None
        source_filename = None

        if isinstance(data, PodcastCreateFromText):
            raw_text = self.input_service.extract_from_text(data.raw_text)
        elif isinstance(data, PodcastCreateFromUrl):
            raw_text, title_from_url = await self.input_service.extract_from_url(str(data.source_url))
            source_url = str(data.source_url)
            if not data.title or data.title == "Untitled":
                data.title = title_from_url
        elif isinstance(data, PodcastCreateFromFile):
            raw_text = self.input_service.extract_from_text(data.raw_text)
            source_filename = data.source_filename

        if not raw_text or len(raw_text.strip()) < 10:
            raise ValueError("Content too short to generate podcast")

        voice_a, voice_b = self.tts_service.get_default_voices(data.language)
        if data.voice_a:
            voice_a = data.voice_a
        if data.voice_b:
            voice_b = data.voice_b

        podcast = Podcast(
            id=podcast_id,
            owner_id=owner_id,
            title=data.title,
            description=data.description,
            source_type=data.source_type,
            source_url=source_url,
            source_filename=source_filename,
            raw_text=raw_text,
            language=data.language,
            voice_a=voice_a,
            voice_b=voice_b,
            silence_ms=data.silence_ms or settings.DEFAULT_SILENCE_MS,
            tags=data.tags or [],
            status="pending",
            progress=0,
        )

        db_session.add(podcast)
        db_session.commit()
        db_session.refresh(podcast)

        logger.info(f"Podcast created: {podcast_id}, source_type={data.source_type}")

        return podcast

    async def process_podcast(
        self,
        db_session,
        podcast: Podcast,
        generate_subtitles: bool = True,
    ) -> Podcast:
        try:
            podcast.status = "scripting"
            podcast.progress = 10
            db_session.commit()

            script = await self.llm_service.generate_script(
                podcast.raw_text,
                title=podcast.title,
                language=podcast.language,
            )

            script_path = f"{settings.SCRIPT_DIR}/{podcast.id}_script.json"
            write_json(script_path, script.model_dump())
            podcast.script_path = script_path
            podcast.progress = 30
            podcast.status = "tts"
            db_session.commit()

            logger.info(f"Script generated for podcast {podcast.id}: {len(script.segments)} segments")

            segments = await self._create_segments(db_session, podcast, script)

            podcast.progress = 50
            podcast.status = "processing"
            db_session.commit()

            audio_paths = []
            for seg in segments:
                if seg.audio_path and os.path.exists(seg.audio_path):
                    audio_paths.append(seg.audio_path)

            if not audio_paths:
                raise ValueError("No audio segments generated")

            podcast.progress = 70
            db_session.commit()

            output_path = f"{settings.AUDIO_DIR}/{podcast.id}_final.{settings.AUDIO_OUTPUT_FORMAT}"
            duration, offsets = self.audio_service.concatenate_segments(
                audio_paths,
                output_path,
                silence_ms=podcast.silence_ms,
            )

            podcast.audio_path = output_path
            podcast.duration_seconds = duration
            podcast.audio_size_bytes = os.path.getsize(output_path)

            for i, seg in enumerate(segments):
                if i < len(offsets):
                    seg.start_offset_seconds = offsets[i]

            self.audio_service.add_metadata(
                output_path,
                title=podcast.title,
                artist=settings.APP_NAME,
                album=settings.APP_NAME,
                year=str(datetime.utcnow().year),
                description=podcast.description or "",
            )

            podcast.progress = 85
            db_session.commit()

            if generate_subtitles:
                try:
                    subtitle_base = f"{settings.SUBTITLE_DIR}/{podcast.id}"
                    subtitle_langs = [podcast.language, "en"] if podcast.language != "en" else ["en"]
                    subtitles = self.subtitle_service.generate_subtitle_files(
                        output_path,
                        subtitle_base,
                        languages=subtitle_langs,
                    )
                    if subtitles:
                        first_sub = list(subtitles.values())[0]
                        podcast.subtitle_path = first_sub.srt_path
                except Exception as e:
                    logger.warning(f"Subtitle generation failed for {podcast.id}: {e}")

            podcast.progress = 100
            podcast.status = "ready"
            podcast.published_at = datetime.utcnow()
            db_session.commit()

            logger.info(f"Podcast {podcast.id} processing complete: {duration:.2f}s")
            return podcast

        except Exception as e:
            logger.error(f"Podcast processing failed for {podcast.id}: {e}", exc_info=True)
            podcast.status = "failed"
            podcast.error_message = str(e)
            db_session.commit()
            raise

    async def _create_segments(
        self,
        db_session,
        podcast: Podcast,
        script: PodcastScript,
    ) -> List[AudioSegment]:
        segments: List[AudioSegment] = []
        total_segments = len(script.segments)

        for i, script_seg in enumerate(script.segments):
            seg_id = gen_id("seg_")
            voice = podcast.voice_a if script_seg.speaker == "A" else podcast.voice_b

            segment = AudioSegment(
                id=seg_id,
                podcast_id=podcast.id,
                order_index=i,
                speaker=script_seg.speaker,
                voice=voice,
                emotion=script_seg.emotion,
                text=script_seg.text,
                status="pending",
            )
            db_session.add(segment)
            segments.append(segment)

        db_session.commit()

        for i, (segment, script_seg) in enumerate(zip(segments, script.segments)):
            try:
                segment.status = "processing"
                db_session.commit()

                audio_path, duration = await self.tts_service.synthesize_segment(
                    script_seg.text,
                    segment.voice,
                    segment.emotion,
                    podcast_id=podcast.id,
                    segment_id=segment.id,
                )

                segment.audio_path = audio_path
                segment.duration_seconds = duration
                segment.audio_size_bytes = os.path.getsize(audio_path)
                segment.status = "ready"

            except Exception as e:
                logger.error(f"TTS failed for segment {segment.id}: {e}")
                segment.status = "failed"
                segment.error_message = str(e)
                segment.retry_count = 1

            db_session.commit()

            progress = 30 + int(40 * (i + 1) / total_segments)
            if progress > podcast.progress:
                podcast.progress = progress
                db_session.commit()

        return segments

    async def retry_segment(
        self,
        db_session,
        segment: AudioSegment,
        retry_data: SegmentRetry,
    ) -> AudioSegment:
        if retry_data.voice:
            segment.voice = retry_data.voice
        if retry_data.emotion:
            segment.emotion = retry_data.emotion
        if retry_data.text:
            segment.text = retry_data.text

        segment.status = "processing"
        segment.retry_count += 1
        db_session.commit()

        try:
            audio_path, duration = await self.tts_service.synthesize_segment(
                segment.text,
                segment.voice,
                segment.emotion,
                podcast_id=segment.podcast_id,
                segment_id=segment.id,
            )

            segment.audio_path = audio_path
            segment.duration_seconds = duration
            segment.audio_size_bytes = os.path.getsize(audio_path)
            segment.status = "ready"
            segment.error_message = None

            db_session.commit()

            podcast = db_session.query(Podcast).get(segment.podcast_id)
            if podcast and podcast.status == "ready":
                await self._rebuild_podcast_audio(db_session, podcast)

            return segment

        except Exception as e:
            segment.status = "failed"
            segment.error_message = str(e)
            db_session.commit()
            raise

    async def _rebuild_podcast_audio(
        self,
        db_session,
        podcast: Podcast,
    ) -> Podcast:
        segments = sorted(podcast.segments, key=lambda s: s.order_index)
        audio_paths = [s.audio_path for s in segments if s.audio_path and os.path.exists(s.audio_path)]

        if not audio_paths:
            return podcast

        output_path = f"{settings.AUDIO_DIR}/{podcast.id}_final.{settings.AUDIO_OUTPUT_FORMAT}"
        duration, offsets = self.audio_service.concatenate_segments(
            audio_paths,
            output_path,
            silence_ms=podcast.silence_ms,
        )

        podcast.audio_path = output_path
        podcast.duration_seconds = duration
        podcast.audio_size_bytes = os.path.getsize(output_path)
        podcast.status = "ready"
        db_session.commit()

        return podcast

    def get_podcast(self, db_session, podcast_id: str) -> Optional[Podcast]:
        return db_session.query(Podcast).get(podcast_id)

    def list_podcasts(
        self,
        db_session,
        owner_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Podcast], int]:
        query = db_session.query(Podcast)
        if owner_id:
            query = query.filter(Podcast.owner_id == owner_id)
        if status:
            query = query.filter(Podcast.status == status)

        total = query.count()
        podcasts = query.order_by(Podcast.created_at.desc()).offset(offset).limit(limit).all()
        return podcasts, total

    def update_podcast(
        self,
        db_session,
        podcast: Podcast,
        data: PodcastUpdate,
    ) -> Podcast:
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(podcast, key, value)
        podcast.updated_at = datetime.utcnow()
        db_session.commit()
        db_session.refresh(podcast)
        return podcast

    def delete_podcast(self, db_session, podcast: Podcast) -> None:
        if podcast.audio_path and os.path.exists(podcast.audio_path):
            try:
                os.remove(podcast.audio_path)
            except Exception:
                pass
        if podcast.script_path and os.path.exists(podcast.script_path):
            try:
                os.remove(podcast.script_path)
            except Exception:
                pass
        for seg in podcast.segments:
            if seg.audio_path and os.path.exists(seg.audio_path):
                try:
                    os.remove(seg.audio_path)
                except Exception:
                    pass

        db_session.delete(podcast)
        db_session.commit()
        logger.info(f"Podcast deleted: {podcast.id}")

    def start_playback(
        self,
        db_session,
        podcast_id: str,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> PlayStat:
        podcast = self.get_podcast(db_session, podcast_id)
        if not podcast:
            raise ValueError("Podcast not found")

        stat_id = gen_id("play_")
        stat = PlayStat(
            id=stat_id,
            podcast_id=podcast_id,
            user_id=user_id,
            session_id=session_id,
            total_seconds=podcast.duration_seconds,
        )
        db_session.add(stat)
        db_session.commit()
        db_session.refresh(stat)
        return stat

    def get_or_create_user(self, db_session, user_data) -> User:
        user = db_session.query(User).filter(User.id == user_data.id).first()
        if user:
            if user_data.name:
                user.name = user_data.name
            if user_data.email:
                user.email = user_data.email
            if user_data.avatar_url:
                user.avatar_url = user_data.avatar_url
            user.updated_at = datetime.utcnow()
        else:
            user = User(
                id=user_data.id or gen_id("usr_"),
                name=user_data.name,
                email=user_data.email,
                avatar_url=user_data.avatar_url,
            )
            db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user
