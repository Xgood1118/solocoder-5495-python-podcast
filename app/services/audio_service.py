import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, AsyncGenerator

from pydub import AudioSegment
from pydub.silence import generate_silence
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TDRC, TCON, COMM, APIC

from app.config import get_settings
from app.utils.helpers import ensure_dir, safe_filename, sync_retry
from app.utils.logging import logger

settings = get_settings()


class AudioProcessError(Exception):
    pass


class AudioService:
    def __init__(self):
        ensure_dir(settings.AUDIO_DIR)
        if settings.FFMPEG_PATH:
            AudioSegment.converter = settings.FFMPEG_PATH
            os.environ["FFMPEG_BINARY"] = settings.FFMPEG_PATH

    @staticmethod
    def _check_ffmpeg() -> None:
        try:
            subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        except Exception:
            try:
                subprocess.run(["ffprobe", "-version"], capture_output=True, check=True)
            except Exception:
                raise AudioProcessError(
                    "ffmpeg is not installed or not in PATH. "
                    "Please install ffmpeg or set FFMPEG_PATH in .env"
                )

    @staticmethod
    def load_audio(filepath: str) -> AudioSegment:
        try:
            return AudioSegment.from_file(filepath)
        except Exception as e:
            logger.error(f"Failed to load audio {filepath}: {e}")
            raise AudioProcessError(f"Failed to load audio: {e}")

    @staticmethod
    def create_silence(duration_ms: int) -> AudioSegment:
        return generate_silence(
            duration_ms,
            sample_rate=settings.AUDIO_SAMPLE_RATE,
        )

    @sync_retry(max_retries=2, delay=2.0, exceptions=(AudioProcessError,))
    def concatenate_segments(
        self,
        audio_paths: List[str],
        output_path: str,
        silence_ms: int = 800,
        segment_offsets: Optional[List[float]] = None,
    ) -> tuple[float, List[float]]:
        if not audio_paths:
            raise AudioProcessError("No audio segments to concatenate")

        self._check_ffmpeg()
        ensure_dir(Path(output_path).parent)

        combined = AudioSegment.empty()
        silence = self.create_silence(silence_ms) if silence_ms > 0 else None
        offsets: List[float] = []
        current_offset = 0.0

        try:
            for i, path in enumerate(audio_paths):
                if not os.path.exists(path):
                    logger.warning(f"Audio file not found, skipping: {path}")
                    offsets.append(current_offset)
                    continue

                segment = self.load_audio(path)
                segment = segment.set_channels(settings.AUDIO_CHANNELS)
                segment = segment.set_frame_rate(settings.AUDIO_SAMPLE_RATE)

                offsets.append(current_offset)
                combined += segment
                current_offset += len(segment) / 1000.0

                if silence and i < len(audio_paths) - 1:
                    combined += silence
                    current_offset += silence_ms / 1000.0

            logger.info(f"Exporting combined audio to {output_path}")
            combined.export(
                output_path,
                format=settings.AUDIO_OUTPUT_FORMAT,
                bitrate=settings.AUDIO_BITRATE,
                parameters=["-ar", str(settings.AUDIO_SAMPLE_RATE), "-ac", str(settings.AUDIO_CHANNELS)],
            )

            file_size = os.path.getsize(output_path)
            duration = len(combined) / 1000.0

            logger.info(
                f"Audio concatenation complete: {output_path}, "
                f"duration={duration:.2f}s, size={file_size} bytes"
            )

            return duration, offsets

        except Exception as e:
            logger.error(f"Audio concatenation failed: {e}")
            raise AudioProcessError(f"Failed to concatenate audio: {e}")

    @sync_retry(max_retries=2, delay=2.0, exceptions=(AudioProcessError,))
    def transcode_audio(
        self,
        input_path: str,
        output_path: str,
        output_format: Optional[str] = None,
        bitrate: Optional[str] = None,
    ) -> tuple[str, float, int]:
        if not os.path.exists(input_path):
            raise AudioProcessError(f"Input file not found: {input_path}")

        self._check_ffmpeg()
        output_format = output_format or settings.AUDIO_OUTPUT_FORMAT
        bitrate = bitrate or settings.AUDIO_BITRATE
        ensure_dir(Path(output_path).parent)

        try:
            audio = self.load_audio(input_path)
            audio = audio.set_channels(settings.AUDIO_CHANNELS)
            audio = audio.set_frame_rate(settings.AUDIO_SAMPLE_RATE)

            audio.export(
                output_path,
                format=output_format,
                bitrate=bitrate,
                parameters=["-ar", str(settings.AUDIO_SAMPLE_RATE), "-ac", str(settings.AUDIO_CHANNELS)],
            )

            duration = len(audio) / 1000.0
            file_size = os.path.getsize(output_path)

            logger.info(
                f"Audio transcoded: {input_path} -> {output_path}, "
                f"format={output_format}, duration={duration:.2f}s"
            )

            return output_path, duration, file_size

        except Exception as e:
            logger.error(f"Audio transcoding failed: {e}")
            raise AudioProcessError(f"Failed to transcode audio: {e}")

    def stream_audio(
        self,
        filepath: str,
        chunk_size: int = 1024 * 1024,
        start_bytes: Optional[int] = None,
        end_bytes: Optional[int] = None,
    ) -> AsyncGenerator[bytes, None]:
        if not os.path.exists(filepath):
            raise AudioProcessError(f"Audio file not found: {filepath}")

        file_size = os.path.getsize(filepath)
        start = start_bytes or 0
        end = end_bytes or (file_size - 1)

        if start >= file_size:
            raise AudioProcessError("Start position beyond file size")

        async def _stream():
            with open(filepath, "rb") as f:
                f.seek(start)
                remaining = end - start + 1
                while remaining > 0:
                    read_size = min(chunk_size, remaining)
                    data = f.read(read_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return _stream()

    def get_audio_info(self, filepath: str) -> dict:
        if not os.path.exists(filepath):
            raise AudioProcessError(f"Audio file not found: {filepath}")

        try:
            audio = MP3(filepath)
            return {
                "duration": float(audio.info.length),
                "bitrate": int(audio.info.bitrate),
                "sample_rate": int(audio.info.sample_rate),
                "channels": int(audio.info.channels),
                "file_size": os.path.getsize(filepath),
            }
        except Exception as e:
            logger.warning(f"Could not get audio info for {filepath}: {e}")
            return {"duration": 0.0, "bitrate": 0, "sample_rate": 0, "channels": 0, "file_size": 0}

    def add_metadata(
        self,
        filepath: str,
        title: str,
        artist: str = "",
        album: str = "",
        year: Optional[str] = None,
        genre: str = "Podcast",
        description: str = "",
        cover_image_path: Optional[str] = None,
    ) -> None:
        try:
            audio = MP3(filepath, ID3=ID3)
            audio.delete()

            audio.tags.add(TIT2(encoding=3, text=title))
            if artist:
                audio.tags.add(TPE1(encoding=3, text=artist))
            if album:
                audio.tags.add(TALB(encoding=3, text=album))
            if year:
                audio.tags.add(TDRC(encoding=3, text=year))
            audio.tags.add(TCON(encoding=3, text=genre))
            if description:
                audio.tags.add(COMM(encoding=3, lang="eng", desc="Description", text=description))

            if cover_image_path and os.path.exists(cover_image_path):
                with open(cover_image_path, "rb") as f:
                    cover_data = f.read()
                mime = "image/jpeg" if cover_image_path.lower().endswith((".jpg", ".jpeg")) else "image/png"
                audio.tags.add(
                    APIC(
                        encoding=3,
                        mime=mime,
                        type=3,
                        desc="Cover",
                        data=cover_data,
                    )
                )

            audio.save()
            logger.info(f"Metadata added to {filepath}")

        except Exception as e:
            logger.warning(f"Could not add metadata to {filepath}: {e}")

    def trim_audio(
        self,
        input_path: str,
        output_path: str,
        start_seconds: float,
        end_seconds: Optional[float] = None,
    ) -> tuple[str, float]:
        if not os.path.exists(input_path):
            raise AudioProcessError(f"Input file not found: {input_path}")

        self._check_ffmpeg()
        ensure_dir(Path(output_path).parent)

        try:
            audio = self.load_audio(input_path)
            start_ms = int(start_seconds * 1000)
            if end_seconds:
                end_ms = int(end_seconds * 1000)
                trimmed = audio[start_ms:end_ms]
            else:
                trimmed = audio[start_ms:]

            trimmed.export(output_path, format=settings.AUDIO_OUTPUT_FORMAT, bitrate=settings.AUDIO_BITRATE)
            duration = len(trimmed) / 1000.0

            return output_path, duration

        except Exception as e:
            logger.error(f"Audio trimming failed: {e}")
            raise AudioProcessError(f"Failed to trim audio: {e}")

    def normalize_audio(self, input_path: str, output_path: str) -> tuple[str, float]:
        if not os.path.exists(input_path):
            raise AudioProcessError(f"Input file not found: {input_path}")

        self._check_ffmpeg()
        ensure_dir(Path(output_path).parent)

        try:
            audio = self.load_audio(input_path)
            normalized = audio.normalize(headroom=0.1)
            normalized.export(output_path, format=settings.AUDIO_OUTPUT_FORMAT, bitrate=settings.AUDIO_BITRATE)
            duration = len(normalized) / 1000.0

            return output_path, duration

        except Exception as e:
            logger.error(f"Audio normalization failed: {e}")
            raise AudioProcessError(f"Failed to normalize audio: {e}")
