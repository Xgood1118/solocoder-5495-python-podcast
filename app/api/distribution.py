from typing import Optional, List
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    DistributionCreate,
    DistributionOut,
)
from app.models import Distribution, Podcast
from app.services.distribution_service import DistributionService
from app.utils.helpers import gen_id
from app.utils.logging import logger

router = APIRouter(prefix="/distribution", tags=["distribution"])

distribution_service = DistributionService()


def _get_owner_id(request: Request) -> str:
    owner_id = request.headers.get("X-User-Id")
    if not owner_id:
        return "default_user"
    return owner_id


@router.get("/platforms")
async def list_platforms():
    platforms = distribution_service.list_platforms()
    return {"platforms": platforms}


@router.get("", response_model=List[DistributionOut])
async def list_distributions(
    podcast_id: Optional[str] = None,
    status: Optional[str] = None,
    platform: Optional[str] = None,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    query = db.query(Distribution).join(Podcast).filter(Podcast.owner_id == owner_id)

    if podcast_id:
        query = query.filter(Distribution.podcast_id == podcast_id)
    if status:
        query = query.filter(Distribution.status == status)
    if platform:
        query = query.filter(Distribution.platform == platform)

    distributions = query.order_by(Distribution.created_at.desc()).all()
    return [DistributionOut.model_validate(d) for d in distributions]


@router.post("", response_model=DistributionOut)
async def create_distribution(
    data: DistributionCreate,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    podcast = db.query(Podcast).get(data.podcast_id)
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")
    if podcast.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Access denied")

    dist_id = gen_id("dist_")
    distribution = Distribution(
        id=dist_id,
        podcast_id=data.podcast_id,
        platform=data.platform,
        webhook_url=str(data.webhook_url) if data.webhook_url else None,
        extra=data.extra or {},
        status="pending",
    )

    db.add(distribution)
    db.commit()
    db.refresh(distribution)

    return DistributionOut.model_validate(distribution)


@router.get("/{distribution_id}", response_model=DistributionOut)
async def get_distribution(
    distribution_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    distribution = (
        db.query(Distribution)
        .join(Podcast)
        .filter(
            Distribution.id == distribution_id,
            Podcast.owner_id == owner_id,
        )
        .first()
    )
    if not distribution:
        raise HTTPException(status_code=404, detail="Distribution not found")
    return DistributionOut.model_validate(distribution)


@router.post("/{distribution_id}/publish", response_model=DistributionOut)
async def publish_distribution(
    distribution_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    distribution = (
        db.query(Distribution)
        .join(Podcast)
        .filter(
            Distribution.id == distribution_id,
            Podcast.owner_id == owner_id,
        )
        .first()
    )
    if not distribution:
        raise HTTPException(status_code=404, detail="Distribution not found")

    podcast = db.query(Podcast).get(distribution.podcast_id)
    if not podcast or podcast.status != "ready":
        raise HTTPException(status_code=400, detail="Podcast not ready for distribution")

    async def _do_publish():
        try:
            await distribution_service.publish_distribution(db, distribution, podcast)
        except Exception as e:
            logger.error(f"Background distribution failed for {distribution_id}: {e}")

    background_tasks.add_task(_do_publish)

    db.refresh(distribution)
    return DistributionOut.model_validate(distribution)


@router.post("/podcast/{podcast_id}/publish-all")
async def publish_all_for_podcast(
    podcast_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    podcast = db.query(Podcast).get(podcast_id)
    if not podcast:
        raise HTTPException(status_code=404, detail="Podcast not found")
    if podcast.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="Access denied")
    if podcast.status != "ready":
        raise HTTPException(status_code=400, detail="Podcast not ready for distribution")

    distributions = (
        db.query(Distribution)
        .filter(
            Distribution.podcast_id == podcast_id,
            Distribution.status.in_(["pending", "failed"]),
        )
        .all()
    )

    async def _do_publish_all():
        try:
            await distribution_service.publish_podcast(db, podcast, distributions)
        except Exception as e:
            logger.error(f"Background distribution failed for podcast {podcast_id}: {e}")

    background_tasks.add_task(_do_publish_all)

    return {
        "status": "started",
        "podcast_id": podcast_id,
        "distribution_count": len(distributions),
    }


@router.delete("/{distribution_id}", status_code=204)
async def delete_distribution(
    distribution_id: str,
    db: Session = Depends(get_db),
    owner_id: str = Depends(_get_owner_id),
):
    distribution = (
        db.query(Distribution)
        .join(Podcast)
        .filter(
            Distribution.id == distribution_id,
            Podcast.owner_id == owner_id,
        )
        .first()
    )
    if not distribution:
        raise HTTPException(status_code=404, detail="Distribution not found")

    db.delete(distribution)
    db.commit()
    return None
