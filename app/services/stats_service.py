import os
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Optional, Dict, Tuple
from pathlib import Path

from sqlalchemy import func, and_

from app.config import get_settings
from app.models import PlayStat, PlayAggregate, Podcast
from app.utils.helpers import ensure_dir
from app.utils.logging import logger

settings = get_settings()

STAY_BUCKETS = [
    (0, 0.1),
    (0.1, 0.25),
    (0.25, 0.5),
    (0.5, 0.75),
    (0.75, 0.9),
    (0.9, 1.0),
]

HOT_THRESHOLD = {
    "min_plays": 50,
    "min_completion_rate": 0.6,
    "hot_score_min": 0.7,
}


class StatsService:
    def __init__(self):
        pass

    @staticmethod
    def _get_date_key(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d")

    @staticmethod
    def _calculate_stay_distribution(
        stay_points: List[float], total_seconds: float, num_buckets: int = 10
    ) -> Dict[str, int]:
        if total_seconds <= 0 or not stay_points:
            return {}

        bucket_counts = defaultdict(int)
        for pos in stay_points:
            if pos <= 0:
                continue
            ratio = pos / total_seconds
            bucket = min(int(ratio * num_buckets), num_buckets - 1)
            bucket_counts[str(bucket)] += 1

        return dict(bucket_counts)

    @staticmethod
    def _calculate_hot_score(
        play_count: int,
        unique_listeners: int,
        completion_rate: float,
        avg_played_seconds: float,
        total_seconds: float,
    ) -> float:
        if total_seconds <= 0 or play_count == 0:
            return 0.0

        avg_ratio = avg_played_seconds / total_seconds if total_seconds > 0 else 0

        play_score = min(play_count / 100.0, 1.0) * 0.3
        unique_score = min(unique_listeners / 50.0, 1.0) * 0.2
        completion_score = completion_rate * 0.3
        avg_play_score = avg_ratio * 0.2

        return round(play_score + unique_score + completion_score + avg_play_score, 4)

    def aggregate_play_stats(
        self,
        db_session,
        podcast_id: str,
        date_key: Optional[str] = None,
    ) -> Optional[PlayAggregate]:
        if date_key is None:
            date_key = self._get_date_key(datetime.utcnow() - timedelta(days=1))

        start_of_day = datetime.strptime(date_key, "%Y-%m-%d")
        end_of_day = start_of_day + timedelta(days=1)

        stats_query = db_session.query(PlayStat).filter(
            PlayStat.podcast_id == podcast_id,
            PlayStat.started_at >= start_of_day,
            PlayStat.started_at < end_of_day,
        )

        total_seconds = (
            db_session.query(func.max(Podcast.duration_seconds))
            .filter(Podcast.id == podcast_id)
            .scalar()
        ) or 0.0

        play_count = stats_query.count()
        if play_count == 0:
            return None

        all_stats = stats_query.all()

        unique_users = set()
        total_played = 0.0
        completed_count = 0
        all_stay_points = []

        for stat in all_stats:
            if stat.user_id:
                unique_users.add(stat.user_id)
            total_played += stat.played_seconds
            if stat.is_complete:
                completed_count += 1
            if stat.stay_points:
                all_stay_points.extend(stat.stay_points)

        unique_listeners = len(unique_users)
        avg_played = total_played / play_count if play_count > 0 else 0.0
        completion_rate = completed_count / play_count if play_count > 0 else 0.0

        stay_distribution = self._calculate_stay_distribution(
            all_stay_points, total_seconds
        )

        hot_score = self._calculate_hot_score(
            play_count,
            unique_listeners,
            completion_rate,
            avg_played,
            total_seconds,
        )

        is_hot = (
            play_count >= HOT_THRESHOLD["min_plays"]
            and completion_rate >= HOT_THRESHOLD["min_completion_rate"]
            and hot_score >= HOT_THRESHOLD["hot_score_min"]
        )

        aggregate = (
            db_session.query(PlayAggregate)
            .filter(
                PlayAggregate.podcast_id == podcast_id,
                PlayAggregate.date_key == date_key,
            )
            .first()
        )

        if aggregate is None:
            aggregate = PlayAggregate(
                podcast_id=podcast_id,
                date_key=date_key,
            )
            db_session.add(aggregate)

        aggregate.play_count = play_count
        aggregate.unique_listeners = unique_listeners
        aggregate.total_played_seconds = total_played
        aggregate.avg_played_seconds = avg_played
        aggregate.completion_rate = round(completion_rate, 4)
        aggregate.hot_score = hot_score
        aggregate.is_hot = is_hot
        aggregate.stay_distribution = stay_distribution

        db_session.commit()

        logger.info(
            f"Aggregated stats for podcast {podcast_id} ({date_key}): "
            f"plays={play_count}, listeners={unique_listeners}, "
            f"completion={completion_rate:.2%}, hot={is_hot}, score={hot_score}"
        )

        return aggregate

    def aggregate_all_podcasts(
        self,
        db_session,
        date_key: Optional[str] = None,
    ) -> int:
        if date_key is None:
            date_key = self._get_date_key(datetime.utcnow() - timedelta(days=1))

        podcasts = db_session.query(Podcast).filter(Podcast.status == "ready").all()

        count = 0
        for podcast in podcasts:
            try:
                self.aggregate_play_stats(db_session, podcast.id, date_key)
                count += 1
            except Exception as e:
                logger.error(f"Failed to aggregate stats for podcast {podcast.id}: {e}")
                db_session.rollback()

        logger.info(f"Completed aggregation for {count} podcasts on {date_key}")
        return count

    def get_hot_podcasts(
        self,
        db_session,
        days: int = 7,
        limit: int = 20,
    ) -> List[Tuple[Podcast, PlayAggregate]]:
        end_date = datetime.utcnow()
        start_date = end_date - timedelta(days=days)

        results = []

        aggregates = (
            db_session.query(PlayAggregate)
            .join(Podcast, PlayAggregate.podcast_id == Podcast.id)
            .filter(
                PlayAggregate.is_hot == True,
                PlayAggregate.date_key >= start_date.strftime("%Y-%m-%d")
            )
            .order_by(PlayAggregate.hot_score.desc())
            .limit(limit)
            .all()
        )

        for agg in aggregates:
            podcast = db_session.query(Podcast).get(agg.podcast_id)
            if podcast:
                results.append((podcast, agg))

        return results

    def get_podcast_stats(
        self,
        db_session,
        podcast_id: str,
        days: int = 30,
    ) -> List[PlayAggregate]:
        start_date = datetime.utcnow() - timedelta(days=days)

        return (
            db_session.query(PlayAggregate)
            .filter(
                PlayAggregate.podcast_id == podcast_id,
                PlayAggregate.date_key >= start_date.strftime("%Y-%m-%d")
            )
            .order_by(PlayAggregate.date_key.asc())
            .all()
        )

    def get_overall_stats(
        self,
        db_session,
        owner_id: Optional[str] = None,
        days: int = 30,
    ) -> Dict:
        start_date = datetime.utcnow() - timedelta(days=days)
        date_key = start_date.strftime("%Y-%m-%d")

        query = db_session.query(
            func.sum(PlayAggregate.play_count).label("total_plays"),
            func.sum(PlayAggregate.unique_listeners).label("total_listeners"),
            func.sum(PlayAggregate.total_played_seconds).label("total_played"),
            func.avg(PlayAggregate.completion_rate).label("avg_completion"),
            func.count(PlayAggregate.is_hot.filter(PlayAggregate.is_hot == True)).label("hot_count"),
        ).filter(PlayAggregate.date_key >= date_key)

        if owner_id:
            query = query.join(Podcast).filter(Podcast.owner_id == owner_id)

        result = query.first()

        total_podcasts_query = db_session.query(Podcast)
        if owner_id:
            total_podcasts_query = total_podcasts_query.filter(Podcast.owner_id == owner_id)
        total_podcasts = total_podcasts_query.count()

        return {
            "total_podcasts": total_podcasts,
            "total_plays": int(result.total_plays or 0),
            "total_listeners": int(result.total_listeners or 0),
            "total_listen_hours": round((result.total_played or 0) / 3600, 2),
            "avg_completion_rate": round(float(result.avg_completion or 0), 4),
            "hot_podcasts": int(result.hot_count or 0),
            "period_days": days,
        }

    def record_play_event(
        self,
        db_session,
        stat_id: str,
        played_seconds: float,
        last_position: float,
        is_complete: bool = False,
        stay_points: Optional[List[float]] = None,
        ended: bool = False,
    ) -> Optional[PlayStat]:
        stat = db_session.query(PlayStat).get(stat_id)
        if not stat:
            return None

        stat.played_seconds = played_seconds
        stat.last_position = last_position
        stat.is_complete = is_complete

        if stay_points:
            existing = stat.stay_points or []
            stat.stay_points = existing + stay_points

        if ended:
            stat.ended_at = datetime.utcnow()

        db_session.commit()
        return stat
