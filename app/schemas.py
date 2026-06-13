from datetime import datetime
from typing import Optional, List, Any, Literal
from pydantic import BaseModel, Field, HttpUrl, field_validator


class UserBase(BaseModel):
    name: str = Field(..., max_length=100)
    email: Optional[str] = Field(None, max_length=200)
    avatar_url: Optional[str] = None


class UserCreate(UserBase):
    id: Optional[str] = None


class UserUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    avatar_url: Optional[str] = None


class UserOut(UserBase):
    id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AudioSegmentOut(BaseModel):
    id: str
    podcast_id: str
    order_index: int
    speaker: str
    voice: Optional[str] = None
    emotion: str
    text: str
    audio_path: Optional[str] = None
    duration_seconds: float = 0.0
    status: str
    error_message: Optional[str] = None
    retry_count: int = 0

    model_config = {"from_attributes": True}


class SegmentRetry(BaseModel):
    voice: Optional[str] = None
    emotion: Optional[str] = None
    text: Optional[str] = None


class PodcastBase(BaseModel):
    title: str = Field(..., max_length=300)
    description: Optional[str] = None
    language: str = "zh"
    voice_a: Optional[str] = None
    voice_b: Optional[str] = None
    silence_ms: int = 800
    tags: Optional[List[str]] = None


class PodcastCreateFromText(PodcastBase):
    source_type: Literal["text"] = "text"
    raw_text: str


class PodcastCreateFromUrl(PodcastBase):
    source_type: Literal["url"] = "url"
    source_url: HttpUrl


class PodcastCreateFromFile(PodcastBase):
    source_type: Literal["file"] = "file"
    source_filename: str
    raw_text: str


PodcastCreate = PodcastCreateFromText | PodcastCreateFromUrl | PodcastCreateFromFile


class PodcastUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    language: Optional[str] = None
    voice_a: Optional[str] = None
    voice_b: Optional[str] = None
    silence_ms: Optional[int] = None
    tags: Optional[List[str]] = None
    cover_url: Optional[str] = None
    published: Optional[bool] = None


class PodcastOut(PodcastBase):
    id: str
    owner_id: str
    source_type: str
    source_url: Optional[str] = None
    source_filename: Optional[str] = None
    status: str
    error_message: Optional[str] = None
    progress: int = 0
    duration_seconds: float = 0.0
    audio_path: Optional[str] = None
    audio_size_bytes: int = 0
    cover_url: Optional[str] = None
    script_path: Optional[str] = None
    subtitle_path: Optional[str] = None
    tags: List[str] = []
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class PodcastDetail(PodcastOut):
    segments: List[AudioSegmentOut] = []


class PodcastFeedBase(BaseModel):
    title: str = Field(..., max_length=300)
    description: Optional[str] = None
    author: Optional[str] = None
    email: Optional[str] = None
    language: str = "zh"
    category: str = "Technology"
    cover_url: Optional[str] = None
    itunes_explicit: bool = False
    is_public: bool = False


class PodcastFeedCreate(PodcastFeedBase):
    pass


class PodcastFeedUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    author: Optional[str] = None
    email: Optional[str] = None
    language: Optional[str] = None
    category: Optional[str] = None
    cover_url: Optional[str] = None
    itunes_explicit: Optional[bool] = None
    is_public: Optional[bool] = None


class PodcastFeedOut(PodcastFeedBase):
    id: str
    owner_id: str
    feed_url: Optional[str] = None
    rss_path: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PlayStatCreate(BaseModel):
    podcast_id: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    played_seconds: float = 0.0
    total_seconds: float = 0.0
    last_position: float = 0.0
    is_complete: bool = False
    stay_points: Optional[List[float]] = None


class PlayStatUpdate(BaseModel):
    played_seconds: Optional[float] = None
    last_position: Optional[float] = None
    is_complete: Optional[bool] = None
    stay_points: Optional[List[float]] = None
    ended: bool = False


class PlayStatOut(BaseModel):
    id: str
    podcast_id: str
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    played_seconds: float = 0.0
    total_seconds: float = 0.0
    last_position: float = 0.0
    is_complete: bool = False
    started_at: datetime
    ended_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class PlayAggregateOut(BaseModel):
    id: int
    podcast_id: str
    date_key: str
    play_count: int = 0
    unique_listeners: int = 0
    total_played_seconds: float = 0.0
    avg_played_seconds: float = 0.0
    completion_rate: float = 0.0
    is_hot: bool = False
    hot_score: float = 0.0
    stay_distribution: dict = {}

    model_config = {"from_attributes": True}


class DistributionBase(BaseModel):
    platform: str
    webhook_url: Optional[str] = None
    extra: Optional[dict] = None


class DistributionCreate(DistributionBase):
    podcast_id: str


class DistributionOut(DistributionBase):
    id: str
    podcast_id: str
    status: str
    external_url: Optional[str] = None
    external_id: Optional[str] = None
    error_message: Optional[str] = None
    published_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class VoiceInfo(BaseModel):
    name: str
    display_name: str
    gender: str
    locale: str


class ScriptSegment(BaseModel):
    speaker: Literal["A", "B"]
    text: str
    emotion: str = "neutral"


class PodcastScript(BaseModel):
    title: str
    summary: str
    language: str
    segments: List[ScriptSegment]


class SubtitleCue(BaseModel):
    index: int
    start: float
    end: float
    text: str


class SubtitleFile(BaseModel):
    language: str
    cues: List[SubtitleCue]
    srt_path: Optional[str] = None
    vtt_path: Optional[str] = None
