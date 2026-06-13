from typing import Optional, List
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import PlayAggregateOut
from app.models import PlayAggregate
from app.services.stats_service import StatsService
from app.utils.logging import logger

router = APIRouter(prefix="/stats", tags=["stats"])

stats_service = StatsService()


def _get_owner_id(request: Request) -> str:
    owner_id = request.headers.get("X-User-Id")
    if not owner_id:
        return "default_user"
    return owner_id


@router.get("/overview")
async def get_overview_stats(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    stats = stats_service.get_overall_stats(db, owner_id, days)
    return stats


@router.get("/hot")
async def get_hot_podcasts(
    days: int = Query(7, ge=1, le=30),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    results = stats_service.get_hot_podcasts(db, days, limit)
    hot_list = []
    for podcast, agg in results:
        hot_list.append({
            "podcast_id": podcast.id,
            "title": podcast.title,
            "description": podcast.description,
            "duration_seconds": podcast.duration_seconds,
            "cover_url": podcast.cover_url,
            "published_at": podcast.published_at,
            "play_count": agg.play_count,
            "unique_listeners": agg.unique_listeners,
            "completion_rate": agg.completion_rate,
            "hot_score": agg.hot_score,
            "date_key": agg.date_key,
        })
    return {"items": hot_list, "total": len(hot_list)}


@router.get("/podcasts/{podcast_id}")
async def get_podcast_stats(
    podcast_id: str,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    from app.models import Podcast

    podcast = db.query(Podcast).get(podcast_id)
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")
    if podcast.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Access denied")

    aggregates = stats_service.get_podcast_stats(db, podcast_id, days)

    total_plays = sum(a.play_count for a in aggregates)
    total_listeners = sum(a.unique_listeners for a in aggregates)
    avg_completion = (
        sum(a.completion_rate for a in aggregates) / len(aggregates)
        if aggregates
        else 0.0
    )

    return {
        "podcast_id": podcast_id,
        "period_days": days,
        "total_plays": total_plays,
        "total_listeners": total_listeners,
        "avg_completion_rate": round(avg_completion, 4),
        "daily_data": [
            {
                "date_key": a.date_key,
                "play_count": a.play_count,
                "unique_listeners": a.unique_listeners,
                "total_played_seconds": a.total_played_seconds,
                "avg_played_seconds": a.avg_played_seconds,
                "completion_rate": a.completion_rate,
                "stay_distribution": a.stay_distribution,
                "is_hot": a.is_hot,
                "hot_score": a.hot_score,
            }
            for a in aggregates
        ],
    }


@router.get("/aggregates/{podcast_id}", response_model=List[PlayAggregateOut])
async def get_podcast_aggregates(
    podcast_id: str,
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    from app.models import Podcast

    podcast = db.query(Podcast).get(podcast_id)
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")
    if podcast.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Access denied")

    aggregates = stats_service.get_podcast_stats(db, podcast_id, days)
    return [PlayAggregateOut.model_validate(a) for a in aggregates]


@router.post("/aggregate")
async def run_aggregation(
    background_tasks: BackgroundTasks,
    date_key: Optional[str] = None,
    podcast_id: Optional[str] = None,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    def _do_aggregate():
        try:
            if podcast_id:
                stats_service.aggregate_play_stats(db, podcast_id, date_key)
            else:
                stats_service.aggregate_all_podcasts(db, date_key)
        except Exception as e:
            logger.error(f"Background aggregation failed: {e}")

    background_tasks.add_task(_do_aggregate)

    return {
        "status": "started",
        "date_key": date_key,
        "podcast_id": podcast_id,
    }


@router.get("/aggregate/recent")
async def get_recent_aggregates(
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    from app.models import Podcast

    aggregates = (
        db.query(PlayAggregate)
        .join(Podcast, PlayAggregate.podcast_id == Podcast.id)
        .filter(Podcast.owner_id == owner_id)
        .order_by(PlayAggregate.date_key.desc(), PlayAggregate.hot_score.desc())
        .limit(limit)
        .all()
    )

    return {
        "items": [
            {
                "podcast_id": a.podcast_id,
                "date_key": a.date_key,
                "play_count": a.play_count,
                "unique_listeners": a.unique_listeners,
                "completion_rate": a.completion_rate,
                "hot_score": a.hot_score,
                "is_hot": a.is_hot,
            }
            for a in aggregates
        ],
        "total": len(aggregates),
    }
