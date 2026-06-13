import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from feedgen.feed import FeedGenerator
from lxml import etree

from app.config import get_settings
from app.models import Podcast, PodcastFeed
from app.utils.helpers import ensure_dir, safe_filename
from app.utils.logging import logger

settings = get_settings()

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
PODCAST_NS = "https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/1.0.md"


class RSSError(Exception):
    pass


class RSSService:
    def __init__(self):
        ensure_dir(settings.RSS_DIR)

    def generate_feed(
        self,
        feed: PodcastFeed,
        podcasts: List[Podcast],
        base_url: str,
    ) -> tuple[str, str]:
        fg = FeedGenerator()

        fg.load_extension("podcast")

        fg.title(feed.title)
        fg.description(feed.description or feed.title)
        fg.link(href=f"{base_url}/rss/{feed.id}", rel="self")
        fg.language(feed.language or "zh")
        fg.generator(f"{settings.APP_NAME} v{settings.APP_VERSION}")
        fg.lastBuildDate(datetime.utcnow())

        if feed.author:
            fg.author({"name": feed.author, "email": feed.email or ""})
        if feed.email:
            fg.managingEditor(f"{feed.email} ({feed.author or ''})")

        if feed.cover_url:
            fg.logo(feed.cover_url)
            fg.image(feed.cover_url, feed.title, f"{base_url}/rss/{feed.id}")

        itunes_explicit = "yes" if feed.itunes_explicit else "no"
        fg.entry(podcast=True)
        fg.podcast.itunes_author(feed.author or feed.title)
        fg.podcast.itunes_explicit(itunes_explicit)
        fg.podcast.itunes_category(feed.category or "Technology")
        fg.podcast.itunes_owner(feed.author or feed.title, feed.email or "")

        if feed.cover_url:
            fg.podcast.itunes_image(feed.cover_url)

        ready_podcasts = [p for p in podcasts if p.status == "ready"]
        ready_podcasts.sort(key=lambda p: p.published_at or p.created_at, reverse=True)

        for podcast in ready_podcasts:
            self._add_podcast_entry(fg, podcast, feed, base_url)

        rss_dir = Path(settings.RSS_DIR)
        rss_dir.mkdir(parents=True, exist_ok=True)
        rss_path = rss_dir / f"{safe_filename(feed.title)}_{feed.id}.xml"

        try:
            rss_content = fg.rss_str(pretty=True)
            with open(rss_path, "wb") as f:
                f.write(rss_content)

            logger.info(f"RSS feed generated: {rss_path}, {len(ready_podcasts)} episodes")
            return str(rss_path), rss_content.decode("utf-8")

        except Exception as e:
            logger.error(f"RSS feed generation failed: {e}")
            raise RSSError(f"Failed to generate RSS feed: {e}")

    def _add_podcast_entry(
        self,
        fg: FeedGenerator,
        podcast: Podcast,
        feed: PodcastFeed,
        base_url: str,
    ) -> None:
        if not podcast.audio_path or not os.path.exists(podcast.audio_path):
            logger.warning(f"Podcast {podcast.id} has no audio, skipping RSS entry")
            return

        fe = fg.add_entry()

        fe.id(f"podcast-{podcast.id}")
        fe.title(podcast.title)
        fe.description(podcast.description or podcast.title)
        fe.pubDate(podcast.published_at or podcast.created_at)
        fe.link(href=f"{base_url}/podcasts/{podcast.id}")

        audio_url = f"{base_url}/podcasts/{podcast.id}/audio"
        fe.enclosure(
            audio_url,
            str(podcast.audio_size_bytes or os.path.getsize(podcast.audio_path)),
            f"audio/{settings.AUDIO_OUTPUT_FORMAT}",
        )

        fe.guid(f"{base_url}/podcasts/{podcast.id}", permalink=True)

        duration = int(podcast.duration_seconds)
        fe.podcast.itunes_duration(duration)

        if podcast.cover_url:
            fe.podcast.itunes_image(podcast.cover_url)

        if podcast.tags:
            keywords = ", ".join(podcast.tags[:10])
            fe.podcast.itunes_keywords(keywords)

    def get_feed_content(self, feed_id: str) -> Optional[str]:
        rss_dir = Path(settings.RSS_DIR)
        for f in rss_dir.glob(f"*{feed_id}.xml"):
            with open(f, "r", encoding="utf-8") as fp:
                return fp.read()
        return None

    def regenerate_all_feeds(
        self,
        db_session,
        base_url: str,
        feed_id: Optional[str] = None,
    ) -> int:
        from app.models import Podcast

        query = db_session.query(PodcastFeed)
        if feed_id:
            query = query.filter(PodcastFeed.id == feed_id)
        feeds = query.all()

        count = 0
        for feed in feeds:
            podcasts = (
                db_session.query(Podcast)
                .filter(
                    Podcast.owner_id == feed.owner_id,
                    Podcast.status == "ready",
                )
                .order_by(Podcast.published_at.desc())
                .all()
            )
            try:
                rss_path, _ = self.generate_feed(feed, podcasts, base_url)
                feed.rss_path = rss_path
                feed.feed_url = f"{base_url}/rss/{feed.id}"
                feed.updated_at = datetime.utcnow()
                db_session.commit()
                count += 1
            except Exception as e:
                logger.error(f"Failed to regenerate feed {feed.id}: {e}")
                db_session.rollback()

        return count

    def validate_feed(self, rss_content: str) -> bool:
        try:
            root = etree.fromstring(rss_content.encode("utf-8"))
            return root.tag == "rss" and "version" in root.attrib
        except Exception as e:
            logger.warning(f"RSS validation failed: {e}")
            return False
