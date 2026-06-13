import json
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Float, DateTime, ForeignKey, Boolean, Index, JSON
from sqlalchemy.orm import relationship

from app.database import Base


def _now():
    return datetime.utcnow()


class User(Base):
    __tablename__ = "users"

    id = Column(String(32), primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(200), unique=True, nullable=True)
    avatar_url = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    podcasts = relationship("Podcast", back_populates="owner", cascade="all, delete-orphan")
    feeds = relationship("PodcastFeed", back_populates="owner", cascade="all, delete-orphan")


class Podcast(Base):
    __tablename__ = "podcasts"

    id = Column(String(32), primary_key=True)
    owner_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(300), nullable=False)
    description = Column(Text, nullable=True)
    source_type = Column(String(20), nullable=False)  # text / url / file
    source_url = Column(String(500), nullable=True)
    source_filename = Column(String(300), nullable=True)
    raw_text = Column(Text, nullable=True)
    language = Column(String(10), default="zh")
    voice_a = Column(String(100), nullable=True)
    voice_b = Column(String(100), nullable=True)
    silence_ms = Column(Integer, default=800)
    status = Column(String(30), default="pending")  # pending / scripting / tts / processing / ready / failed
    error_message = Column(Text, nullable=True)
    progress = Column(Integer, default=0)
    duration_seconds = Column(Float, default=0.0)
    audio_path = Column(String(500), nullable=True)
    audio_size_bytes = Column(Integer, default=0)
    cover_url = Column(String(500), nullable=True)
    script_path = Column(String(500), nullable=True)
    subtitle_path = Column(String(500), nullable=True)
    tags = Column(JSON, default=list)
    extra = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_now, index=True)
    updated_at = Column(DateTime, default=_now, onupdate=_now, index=True)
    published_at = Column(DateTime, nullable=True, index=True)

    owner = relationship("User", back_populates="podcasts")
    segments = relationship("AudioSegment", back_populates="podcast", cascade="all, delete-orphan", order_by="AudioSegment.order_index")
    play_stats = relationship("PlayStat", back_populates="podcast", cascade="all, delete-orphan")
    distributions = relationship("Distribution", back_populates="podcast", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_podcast_owner_status", "owner_id", "status"),
    )


class AudioSegment(Base):
    __tablename__ = "audio_segments"

    id = Column(String(32), primary_key=True)
    podcast_id = Column(String(32), ForeignKey("podcasts.id"), nullable=False, index=True)
    order_index = Column(Integer, nullable=False)
    speaker = Column(String(20), nullable=False)  # A / B
    voice = Column(String(100), nullable=True)
    emotion = Column(String(50), default="neutral")  # neutral / happy / serious / curious / excited
    text = Column(Text, nullable=False)
    audio_path = Column(String(500), nullable=True)
    audio_size_bytes = Column(Integer, default=0)
    duration_seconds = Column(Float, default=0.0)
    start_offset_seconds = Column(Float, default=0.0)
    status = Column(String(30), default="pending")  # pending / processing / ready / failed
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    podcast = relationship("Podcast", back_populates="segments")


class PodcastFeed(Base):
    __tablename__ = "podcast_feeds"

    id = Column(String(32), primary_key=True)
    owner_id = Column(String(32), ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(300), nullable=False)
    description = Column(Text, nullable=True)
    author = Column(String(200), nullable=True)
    email = Column(String(200), nullable=True)
    language = Column(String(10), default="zh")
    category = Column(String(100), default="Technology")
    cover_url = Column(String(500), nullable=True)
    feed_url = Column(String(500), nullable=True)
    rss_path = Column(String(500), nullable=True)
    itunes_explicit = Column(Boolean, default=False)
    is_public = Column(Boolean, default=False)
    extra = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    owner = relationship("User", back_populates="feeds")


class PlayStat(Base):
    __tablename__ = "play_stats"

    id = Column(String(32), primary_key=True)
    podcast_id = Column(String(32), ForeignKey("podcasts.id"), nullable=False, index=True)
    user_id = Column(String(64), nullable=True, index=True)
    session_id = Column(String(64), nullable=True, index=True)
    played_seconds = Column(Float, default=0.0)
    total_seconds = Column(Float, default=0.0)
    last_position = Column(Float, default=0.0)
    is_complete = Column(Boolean, default=False)
    stay_points = Column(JSON, default=list)  # list of position_seconds
    started_at = Column(DateTime, default=_now, index=True)
    ended_at = Column(DateTime, nullable=True)

    podcast = relationship("Podcast", back_populates="play_stats")

    __table_args__ = (
        Index("ix_playstat_podcast_started", "podcast_id", "started_at"),
    )


class PlayAggregate(Base):
    __tablename__ = "play_aggregates"

    id = Column(Integer, primary_key=True, autoincrement=True)
    podcast_id = Column(String(32), ForeignKey("podcasts.id"), nullable=False, index=True)
    date_key = Column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    play_count = Column(Integer, default=0)
    unique_listeners = Column(Integer, default=0)
    total_played_seconds = Column(Float, default=0.0)
    avg_played_seconds = Column(Float, default=0.0)
    completion_rate = Column(Float, default=0.0)
    is_hot = Column(Boolean, default=False)
    hot_score = Column(Float, default=0.0)
    stay_distribution = Column(JSON, default=dict)  # bucket -> count
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    __table_args__ = (
        Index("ix_agg_podcast_date", "podcast_id", "date_key", unique=True),
    )


class Distribution(Base):
    __tablename__ = "distributions"

    id = Column(String(32), primary_key=True)
    podcast_id = Column(String(32), ForeignKey("podcasts.id"), nullable=False, index=True)
    platform = Column(String(50), nullable=False)  # ximalaya / netease / qq / spotify / generic_webhook
    status = Column(String(30), default="pending")  # pending / publishing / published / failed
    external_url = Column(String(500), nullable=True)
    external_id = Column(String(200), nullable=True)
    error_message = Column(Text, nullable=True)
    webhook_url = Column(String(500), nullable=True)
    extra = Column(JSON, default=dict)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_now)
    updated_at = Column(DateTime, default=_now, onupdate=_now)

    podcast = relationship("Podcast", back_populates="distributions")
