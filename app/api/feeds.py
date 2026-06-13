import os
from typing import Optional, List
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import get_settings
from app.schemas import (
    PodcastFeedCreate,
    PodcastFeedUpdate,
    PodcastFeedOut,
)
from app.models import PodcastFeed, Podcast
from app.services.rss_service import RSSService
from app.utils.logging import logger

settings = get_settings()

router = APIRouter(prefix="/feeds", tags=["feeds"])

rss_service = RSSService()


def _get_owner_id(request: Request) -> str:
    owner_id = request.headers.get("X-User-Id")
    if not owner_id:
        return "default_user"
    return owner_id


def _get_base_url(request: Request) -> str:
    scheme = request.url.scheme
    netloc = request.url.netloc
    return f"{scheme}://{netloc}"


@router.get("", response_model=List[PodcastFeedOut])
async def list_feeds(
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    feeds = db.query(PodcastFeed).filter(
        PodcastFeed.owner_id == owner_id
    ).order_by(PodcastFeed.created_at.desc()).all()
    return [PodcastFeedOut.model_validate(f) for f in feeds]


@router.post("", response_model=PodcastFeedOut)
async def create_feed(
    data: PodcastFeedCreate,
    request: Request,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    from app.utils.helpers import gen_id

    feed = PodcastFeed(
        id=gen_id("feed_"),
        owner_id=owner_id,
        **data.model_dump(exclude_unset=True)
    )
    db.add(feed)
    db.commit()
    db.refresh(feed)

    try:
        base_url = _get_base_url(request)
        podcasts = db.query(Podcast).filter(
            Podcast.owner_id == owner_id,
            Podcast.status == "ready",
        ).order_by(Podcast.published_at.desc()).all()

        rss_path, _ = rss_service.generate_feed(feed, podcasts, base_url)
        feed.rss_path = rss_path
        feed.feed_url = f"{base_url}/rss/{feed.id}"
        db.commit()
        db.refresh(feed)
    except Exception as e:
        logger.warning(f"Could not generate initial RSS for feed {feed.id}: {e}")

    return PodcastFeedOut.model_validate(feed)


@router.get("/{feed_id}", response_model=PodcastFeedOut)
async def get_feed(
    feed_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    feed = db.query(PodcastFeed).get(feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    if feed.owner_id != owner_id and not feed.is_public:
        raise HTTPException(status_code=403, detail="Access denied")
    return PodcastFeedOut.model_validate(feed)


@router.patch("/{feed_id}", response_model=PodcastFeedOut)
async def update_feed(
    feed_id: str,
    data: PodcastFeedUpdate,
    request: Request,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    feed = db.query(PodcastFeed).get(feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    if feed.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Access denied")

    update_data = data.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(feed, key, value)
    db.commit()
    db.refresh(feed)

    try:
        base_url = _get_base_url(request)
        rss_service.regenerate_all_feeds(db, base_url, feed_id=feed.id)
        db.refresh(feed)
    except Exception as e:
        logger.warning(f"Could not regenerate RSS for feed {feed.id}: {e}")

    return PodcastFeedOut.model_validate(feed)


@router.delete("/{feed_id}", status_code=204)
async def delete_feed(
    feed_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    feed = db.query(PodcastFeed).get(feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    if feed.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Access denied")

    if feed.rss_path and os.path.exists(feed.rss_path):
        try:
            os.remove(feed.rss_path)
        except Exception:
            pass

    db.delete(feed)
    db.commit()
    return None


@router.post("/{feed_id}/regenerate")
async def regenerate_feed(
    feed_id: str,
    request: Request,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    feed = db.query(PodcastFeed).get(feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    if feed.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Access denied")

    base_url = _get_base_url(request)
    count = rss_service.regenerate_all_feeds(db, base_url, feed_id=feed.id)
    return {"regenerated": count > 0, "feed_id": feed_id}


@router.get("/rss/{feed_id}")
async def get_rss_feed(
    feed_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    feed = db.query(PodcastFeed).get(feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    if not feed.rss_path or not os.path.exists(feed.rss_path):
        base_url = _get_base_url(request)
        rss_service.regenerate_all_feeds(db, base_url, feed_id=feed.id)
        db.refresh(feed)

    if feed.rss_path and os.path.exists(feed.rss_path):
        with open(feed.rss_path, "r", encoding="utf-8") as f:
            content = f.read()
        return Response(content, media_type="application/rss+xml")

    content = rss_service.get_feed_content(feed_id)
    if content:
        return Response(content, media_type="application/rss+xml")

    raise HTTPException(status_code=404, detail="RSS feed not available")
