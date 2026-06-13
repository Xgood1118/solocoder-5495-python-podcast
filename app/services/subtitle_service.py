import os
from pathlib import Path
from typing import List, Optional, Dict

import whisper

from app.config import get_settings
from app.schemas import SubtitleCue, SubtitleFile
from app.utils.helpers import ensure_dir, sync_retry, write_json
from app.utils.logging import logger

settings = get_settings()


class SubtitleError(Exception):
    pass


class SubtitleService:
    def __init__(self):
        ensure_dir(settings.SUBTITLE_DIR)
        self._model = None

    def _load_model(self, model_name: Optional[str] = None):
        if self._model is None:
            try:
                model_name = model_name or settings.WHISPER_MODEL
                logger.info(f"Loading Whisper model: {model_name}")
                self._model = whisper.load_model(model_name)
                logger.info(f"Whisper model loaded successfully")
            except Exception as e:
                logger.error(f"Failed to load Whisper model: {e}")
                raise SubtitleError(f"Failed to load Whisper model: {e}")
        return self._model

    @sync_retry(max_retries=2, delay=3.0, exceptions=(SubtitleError,))
    def transcribe_audio(
        self,
        audio_path: str,
        language: Optional[str] = None,
        task: str = "transcribe",
    ) -> SubtitleFile:
        if not os.path.exists(audio_path):
            raise SubtitleError(f"Audio file not found: {audio_path}")

        model = self._load_model()
        language = language or settings.WHISPER_LANGUAGE

        try:
            logger.info(f"Transcribing audio: {audio_path}, language={language}, task={task}")

            result = model.transcribe(
                audio_path,
                language=language,
                task=task,
                verbose=False,
            )

            cues: List[SubtitleCue] = []
            for i, seg in enumerate(result.get("segments", []), 1):
                cues.append(
                    SubtitleCue(
                        index=i,
                        start=float(seg.get("start", 0)),
                        end=float(seg.get("end", 0)),
                        text=seg.get("text", "").strip(),
                    )
                )

            detected_language = result.get("language", language)

            subtitle = SubtitleFile(
                language=detected_language,
                cues=cues,
            )

            logger.info(
                f"Transcription complete: {len(cues)} cues, "
                f"language={detected_language}"
            )

            return subtitle

        except Exception as e:
            logger.error(f"Audio transcription failed: {e}")
            raise SubtitleError(f"Failed to transcribe audio: {e}")

    @sync_retry(max_retries=2, delay=3.0, exceptions=(SubtitleError,))
    def translate_audio(
        self,
        audio_path: str,
        target_language: str = "en",
    ) -> SubtitleFile:
        return self.transcribe_audio(audio_path, language=target_language, task="translate")

    def generate_srt(self, subtitle: SubtitleFile, output_path: str) -> str:
        ensure_dir(Path(output_path).parent)

        def _format_time(seconds: float) -> str:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            millis = int((seconds - int(seconds)) * 1000)
            return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

        try:
            srt_lines = []
            for cue in subtitle.cues:
                srt_lines.append(str(cue.index))
                srt_lines.append(
                    f"{_format_time(cue.start)} --> {_format_time(cue.end)}"
                )
                srt_lines.append(cue.text)
                srt_lines.append("")

            srt_content = "\n".join(srt_lines)

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(srt_content)

            logger.info(f"SRT subtitle generated: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"SRT generation failed: {e}")
            raise SubtitleError(f"Failed to generate SRT: {e}")

    def generate_vtt(self, subtitle: SubtitleFile, output_path: str) -> str:
        ensure_dir(Path(output_path).parent)

        def _format_time(seconds: float) -> str:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            millis = int((seconds - int(seconds)) * 1000
            return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"

        try:
            vtt_lines = ["WEBVTT", ""]
            for cue in subtitle.cues:
                vtt_lines.append(
                    f"{_format_time(cue.start)} --> {_format_time(cue.end)}"
                )
                vtt_lines.append(cue.text)
                vtt_lines.append("")

            vtt_content = "\n".join(vtt_lines)

            with open(output_path, "w", encoding="utf-8") as f:
                f.write(vtt_content)

            logger.info(f"VTT subtitle generated: {output_path}")
            return output_path

        except Exception as e:
            logger.error(f"VTT generation failed: {e}")
            raise SubtitleError(f"Failed to generate VTT: {e}")

    def generate_subtitle_files(
        self,
        audio_path: str,
        base_output_path: str,
        languages: Optional[List[str]] = None,
    ) -> Dict[str, SubtitleFile]:
        languages = languages or [settings.WHISPER_LANGUAGE]
        results: Dict[str, SubtitleFile] = {}

        for lang in languages:
            try:
                task = "translate" if lang != settings.WHISPER_LANGUAGE else "transcribe"
                subtitle = self.transcribe_audio(audio_path, language=lang, task=task)
                subtitle.language = lang

                srt_path = f"{base_output_path}_{lang}.srt"
                vtt_path = f"{base_output_path}_{lang}.vtt"
                json_path = f"{base_output_path}_{lang}.json"

                self.generate_srt(subtitle, srt_path)
                self.generate_vtt(subtitle, vtt_path)
                write_json(json_path, subtitle.model_dump())

                subtitle.srt_path = srt_path
                subtitle.vtt_path = vtt_path

                results[lang] = subtitle

            except Exception as e:
                logger.warning(f"Failed to generate {lang} subtitle: {e}")

        return results

    def merge_subtitles(self, subtitles: List[SubtitleFile]) -> SubtitleFile:
        all_cues = []
        for sub in subtitles:
            all_cues.extend(sub.cues)
        all_cues.sort(key=lambda c: c.start)
        for i, cue in enumerate(all_cues, 1):
            cue.index = i
        return SubtitleFile(
            language=subtitles[0].language if subtitles else "zh",
            cues=all_cues,
        )

    def get_cue_at_time(self, subtitle: SubtitleFile, time_seconds: float) -> Optional[SubtitleCue]:
        for cue in subtitle.cues:
            if cue.start <= time_seconds <= cue.end:
                return cue
        return None
