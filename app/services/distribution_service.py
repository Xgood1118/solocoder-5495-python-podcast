import json
from datetime import datetime
from typing import Optional, Dict, List
from pathlib import Path
import httpx

from app.config import get_settings
from app.models import Distribution, Podcast
from app.utils.helpers import async_retry
from app.utils.logging import logger

settings = get_settings()


class DistributionError(Exception):
    pass


class DistributionService:
    PLATFORMS = {
        "ximalaya": "喜马拉雅",
        "netease": "网易云音乐",
        "qqmusic": "QQ音乐",
        "spotify": "Spotify",
        "generic_webhook": "通用Webhook",
        "feishu": "飞书",
        "dingtalk": "钉钉",
        "wecom": "企业微信",
        "slack": "Slack",
        "email": "邮件通知",
    }

    def __init__(self):
        pass

    @async_retry(max_retries=3, delay=2.0, exceptions=(DistributionError,))
    async def publish_to_webhook(
        self,
        webhook_url: str,
        podcast: Podcast,
        extra: Optional[Dict] = None,
    ) -> Dict:
        payload = {
            "event": "podcast_published",
            "podcast_id": podcast.id,
            "title": podcast.title,
            "description": podcast.description,
            "duration_seconds": podcast.duration_seconds,
            "audio_url": podcast.audio_path,
            "cover_url": podcast.cover_url,
            "tags": podcast.tags,
            "published_at": podcast.published_at.isoformat() if podcast.published_at else None,
            "extra": extra or {},
            "timestamp": datetime.utcnow().isoformat(),
        }

        if podcast.audio_path and Path(podcast.audio_path).exists():
            file_size = Path(podcast.audio_path).stat().st_size
            payload["audio_size_bytes"] = file_size

        try:
            logger.info(f"Publishing to webhook: {webhook_url}")

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()

                try:
                    return response.json()
                except Exception:
                    return {"status": "success", "text": response.text}

        except httpx.HTTPError as e:
            logger.error(f"Webhook publish failed: {e}")
            raise DistributionError(f"Webhook publish failed: {e}")

    @async_retry(max_retries=3, delay=2.0, exceptions=(DistributionError,))
    async def publish_to_feishu(
        self,
        webhook_url: str,
        podcast: Podcast,
    ) -> Dict:
        message = {
            "msg_type": "interactive",
            "card": {
                "config": {"wide_screen_mode": True},
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": f"🎙️ 新播客发布：{podcast.title}",
                    },
                    "template": "blue",
                },
                "elements": [
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": podcast.description or podcast.title,
                        },
                    },
                    {
                        "tag": "div",
                        "fields": [
                            {
                                "is_short": True,
                                "text": {
                                    "tag": "lark_md",
                                    "content": f"**时长**\n{int(podcast.duration_seconds // 60)}:{int(podcast.duration_seconds % 60):02d}",
                                },
                            },
                            {
                                "is_short": True,
                                "text": {
                                    "tag": "lark_md",
                                    "content": f"**标签**\n{', '.join(podcast.tags) if podcast.tags else '无'}",
                                },
                            },
                        ],
                    },
                ],
            },
        }

        try:
            logger.info("Publishing to Feishu")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(webhook_url, json=message)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Feishu publish failed: {e}")
            raise DistributionError(f"Feishu publish failed: {e}")

    @async_retry(max_retries=3, delay=2.0, exceptions=(DistributionError,))
    async def publish_to_dingtalk(
        self,
        webhook_url: str,
        podcast: Podcast,
    ) -> Dict:
        duration_str = f"{int(podcast.duration_seconds // 60}:{int(podcast.duration_seconds % 60):02d}"

        message = {
            "msgtype": "markdown",
            "markdown": {
                "title": f"新播客：{podcast.title}",
                "text": f"# 🎙️ {podcast.title}\n\n**时长**: {duration_str}\n\n{podcast.description or ''}\n\n**标签**: {', '.join(podcast.tags) if podcast.tags else '无'}",
            },
        }

        try:
            logger.info("Publishing to DingTalk")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(webhook_url, json=message)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as e:
            logger.error(f"DingTalk publish failed: {e}")
            raise DistributionError(f"DingTalk publish failed: {e}")

    @async_retry(max_retries=3, delay=2.0, exceptions=(DistributionError,))
    async def publish_to_wecom(
        self,
        webhook_url: str,
        podcast: Podcast,
    ) -> Dict:
        duration_str = f"{int(podcast.duration_seconds // 60}:{int(podcast.duration_seconds % 60):02d}"

        message = {
            "msgtype": "markdown",
            "markdown": {
                "content": f"## 🎙️ {podcast.title}\n\n> **时长**: {duration_str}\n\n{podcast.description or ''}\n\n**标签**: {', '.join(podcast.tags) if podcast.tags else '无'}",
            },
        }

        try:
            logger.info("Publishing to WeCom")
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(webhook_url, json=message)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as e:
            logger.error(f"WeCom publish failed: {e}")
            raise DistributionError(f"WeCom publish failed: {e}")

    async def publish_distribution(
        self,
        db_session,
        distribution: Distribution,
        podcast: Podcast,
    ) -> Distribution:
        distribution.status = "publishing"
        db_session.commit()

        try:
            if distribution.platform == "generic_webhook":
                result = await self.publish_to_webhook(
                    distribution.webhook_url, podcast, distribution.extra)
            elif distribution.platform == "feishu":
                result = await self.publish_to_feishu(
                    distribution.webhook_url, podcast)
            elif distribution.platform == "dingtalk":
                result = await self.publish_to_dingtalk(
                    distribution.webhook_url, podcast)
            elif distribution.platform == "wecom":
                result = await self.publish_to_wecom(
                    distribution.webhook_url, podcast)
            elif distribution.platform in ("ximalaya", "netease", "qqmusic", "spotify"):
                result = await self.publish_to_webhook(
                    distribution.webhook_url, podcast, distribution.extra)
            else:
                raise DistributionError(f"Unsupported platform: {distribution.platform}")

            distribution.status = "published"
            distribution.published_at = datetime.utcnow()
            distribution.external_url = result.get("url") if isinstance(result, dict) else None
            distribution.external_id = result.get("id") if isinstance(result, dict) else None
            distribution.extra = {**(distribution.extra or {}), "publish_result": result}

        except Exception as e:
            distribution.status = "failed"
            distribution.error_message = str(e)
            logger.error(f"Distribution failed for {distribution.id}: {e}")

        distribution.updated_at = datetime.utcnow()
        db_session.commit()
        return distribution

    async def publish_podcast(
        self,
        db_session,
        podcast: Podcast,
        distributions: Optional[List[Distribution]],
    ) -> List[Distribution]:
        results = []
        for dist in distributions:
            try:
                result = await self.publish_distribution(db_session, dist, podcast)
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to publish distribution {dist.id}: {e}")
        return results

    @staticmethod
    def list_platforms() -> List[Dict]:
        return [
            {"key": k, "name": v, "type": "webhook" if k in ("generic_webhook", "feishu", "dingtalk", "wecom", "slack") else "platform"}
            for k, v in DistributionService.PLATFORMS.items()
        ]
