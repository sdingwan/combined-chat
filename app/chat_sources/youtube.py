import asyncio
import dataclasses
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx
from app.config import settings
from app.db import AsyncSessionMaker
from app.models import YouTubeChannelCache, YouTubeLiveChatCache

logger = logging.getLogger(__name__)

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
LIVE_CHAT_MESSAGES_ENDPOINT = f"{YOUTUBE_API_BASE}/liveChat/messages"
LIVE_BROADCASTS_ENDPOINT = f"{YOUTUBE_API_BASE}/liveBroadcasts"
CHANNELS_ENDPOINT = f"{YOUTUBE_API_BASE}/channels"
SEARCH_ENDPOINT = f"{YOUTUBE_API_BASE}/search"
VIDEOS_ENDPOINT = f"{YOUTUBE_API_BASE}/videos"

CHANNEL_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 hours
LIVE_CHAT_CACHE_TTL_SECONDS = 5 * 60     # 5 minutes
LIVE_CHAT_FAILURE_TTL_SECONDS = 120      # 2 minutes


@dataclasses.dataclass
class ChannelCacheEntry:
    channel_id: Optional[str]
    title: Optional[str]
    thumbnail: Optional[str]
    expires_at: float


@dataclasses.dataclass
class LiveChatCacheEntry:
    live_chat_id: str
    video_id: Optional[str]
    expires_at: float


_channel_cache: dict[str, ChannelCacheEntry] = {}
_live_chat_cache: dict[str, LiveChatCacheEntry] = {}
_live_chat_failures: dict[str, float] = {}


def _cache_now() -> float:
    return time.monotonic()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _cache_channel_entry(key: str, entry: ChannelCacheEntry) -> None:
    _channel_cache[key.lower()] = entry


def _get_cached_channel(key: str) -> Optional[ChannelCacheEntry]:
    cached = _channel_cache.get(key.lower())
    if cached and cached.expires_at > _cache_now():
        return cached
    if cached:
        _channel_cache.pop(key.lower(), None)
    return None


def _cache_live_chat_entry(key: str, entry: LiveChatCacheEntry) -> None:
    if not key:
        return
    _live_chat_cache[key.lower()] = entry


def _get_cached_live_chat(*keys: str) -> Optional[LiveChatCacheEntry]:
    for key in keys:
        if not key:
            continue
        candidate = _live_chat_cache.get(key.lower())
        if candidate and candidate.expires_at > _cache_now():
            return candidate
        if candidate:
            _live_chat_cache.pop(key.lower(), None)
    return None


def invalidate_live_chat_cache(channel_id: Optional[str], handle: Optional[str] = None) -> None:
    for key in (channel_id, handle):
        if not key:
            continue
        key_lower = key.lower()
        _live_chat_cache.pop(key_lower, None)
        _live_chat_failures.pop(key_lower, None)


async def _load_channel_cache_db(handle: str) -> Optional[ChannelCacheEntry]:
    if not handle:
        return None
    async with AsyncSessionMaker() as session:
        record = await session.get(YouTubeChannelCache, handle)
        if not record:
            return None
        expires_at = _ensure_aware(record.expires_at)
        now = _utcnow()
        if expires_at <= now:
            await session.delete(record)
            await session.commit()
            return None
        ttl_remaining = max(0.0, (expires_at - now).total_seconds())
        entry = ChannelCacheEntry(
            channel_id=record.channel_id,
            title=record.title,
            thumbnail=record.thumbnail_url,
            expires_at=_cache_now() + ttl_remaining,
        )
        return entry


async def _persist_channel_cache_db(
    handle: str,
    *,
    channel_id: Optional[str],
    title: Optional[str],
    thumbnail: Optional[str],
) -> None:
    if not handle:
        return
    expires_at = _utcnow() + timedelta(seconds=CHANNEL_CACHE_TTL_SECONDS)
    async with AsyncSessionMaker() as session:
        record = await session.get(YouTubeChannelCache, handle)
        if record:
            record.channel_id = channel_id
            record.title = title
            record.thumbnail_url = thumbnail
            record.expires_at = expires_at
        else:
            record = YouTubeChannelCache(
                handle=handle,
                channel_id=channel_id,
                title=title,
                thumbnail_url=thumbnail,
                expires_at=expires_at,
            )
            session.add(record)
        await session.commit()


async def _load_live_chat_cache_db(handle: str) -> Optional[LiveChatCacheEntry]:
    if not handle:
        return None
    async with AsyncSessionMaker() as session:
        record = await session.get(YouTubeLiveChatCache, handle)
        if not record:
            return None
        expires_at = _ensure_aware(record.expires_at)
        now = _utcnow()
        if expires_at <= now or not record.live_chat_id:
            await session.delete(record)
            await session.commit()
            return None
        ttl_remaining = max(0.0, (expires_at - now).total_seconds())
        entry = LiveChatCacheEntry(
            live_chat_id=record.live_chat_id,
            video_id=record.video_id,
            expires_at=_cache_now() + ttl_remaining,
        )
        return entry


async def _persist_live_chat_cache_db(
    handle: str,
    *,
    channel_id: Optional[str],
    live_chat_id: Optional[str],
    video_id: Optional[str],
) -> None:
    if not handle or not live_chat_id:
        return
    expires_at = _utcnow() + timedelta(seconds=LIVE_CHAT_CACHE_TTL_SECONDS)
    async with AsyncSessionMaker() as session:
        record = await session.get(YouTubeLiveChatCache, handle)
        if record:
            record.channel_id = channel_id
            record.live_chat_id = live_chat_id
            record.video_id = video_id
            record.expires_at = expires_at
        else:
            record = YouTubeLiveChatCache(
                handle=handle,
                channel_id=channel_id,
                live_chat_id=live_chat_id,
                video_id=video_id,
                expires_at=expires_at,
            )
            session.add(record)
        await session.commit()


async def _resolve_channel_metadata_with_client(
    client: httpx.AsyncClient,
    slug: str,
    api_key: Optional[str],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    slug_key = slug.lower()
    cached = _get_cached_channel(slug_key)
    if cached:
        return cached.channel_id, cached.title, cached.thumbnail

    db_cached = await _load_channel_cache_db(slug_key)
    if db_cached:
        _cache_channel_entry(slug_key, db_cached)
        if db_cached.channel_id:
            _cache_channel_entry(f"id:{db_cached.channel_id.lower()}", db_cached)
        return db_cached.channel_id, db_cached.title, db_cached.thumbnail

    channel_id: Optional[str] = None
    title: Optional[str] = None
    thumbnail: Optional[str] = None

    attempts: list[dict[str, str]] = []
    if not slug.startswith("@"):
        return None, None, None
    attempts.append({"forHandle": slug.lstrip("@")})

    params_base = {
        "part": "id,snippet",
        "maxResults": 1,
    }

    for attempt in attempts:
        params = {**params_base, **attempt}
        if api_key:
            params["key"] = api_key
        response = await client.get(CHANNELS_ENDPOINT, params=params)
        if response.status_code in {401, 403} and api_key:
            break
        if response.status_code != 200:
            continue
        payload = response.json()
        items = payload.get("items") if isinstance(payload, dict) else None
        if isinstance(items, list) and items:
            entry = items[0] if isinstance(items[0], dict) else {}
            channel_id, title, thumbnail = YouTubeChatClient._extract_channel_metadata(entry)
            if channel_id:
                cache_entry = ChannelCacheEntry(
                    channel_id=channel_id,
                    title=title,
                    thumbnail=thumbnail,
                    expires_at=_cache_now() + CHANNEL_CACHE_TTL_SECONDS,
                )
                _cache_channel_entry(slug_key, cache_entry)
                if channel_id:
                    _cache_channel_entry(f"id:{channel_id.lower()}", cache_entry)
                await _persist_channel_cache_db(
                    slug_key,
                    channel_id=channel_id,
                    title=title,
                    thumbnail=thumbnail,
                )
                return channel_id, title, thumbnail
    return channel_id, title, thumbnail


async def _resolve_live_chat_id_with_client(
    client: httpx.AsyncClient,
    channel_id: str,
    api_key: Optional[str],
    *,
    channel_slug: Optional[str] = None,
) -> Optional[str]:
    handle = (channel_slug or "").lower() if channel_slug else None

    cache_key = channel_id.lower()
    cached = _get_cached_live_chat(cache_key, handle)
    if cached:
        return cached.live_chat_id

    db_cached = await _load_live_chat_cache_db(handle) if handle else None
    if db_cached:
        _cache_live_chat_entry(channel_id, db_cached)
        if handle:
            _cache_live_chat_entry(handle, db_cached)
        return db_cached.live_chat_id

    failure_mark = _live_chat_failures.get(cache_key)
    if failure_mark and failure_mark + LIVE_CHAT_FAILURE_TTL_SECONDS > _cache_now():
        return None

    if not api_key:
        _live_chat_failures[cache_key] = _cache_now()
        return None

    live_chat_id, video_id = await fetch_live_chat_id_for_channel(
        client,
        api_key=api_key,
        channel_id=channel_id,
        channel_slug=channel_slug,
    )
    if live_chat_id:
        entry = LiveChatCacheEntry(
            live_chat_id=live_chat_id,
            video_id=video_id,
            expires_at=_cache_now() + LIVE_CHAT_CACHE_TTL_SECONDS,
        )
        _cache_live_chat_entry(channel_id, entry)
        if handle:
            _cache_live_chat_entry(handle, entry)
        if handle:
            await _persist_live_chat_cache_db(
                handle,
                channel_id=channel_id,
                live_chat_id=live_chat_id,
                video_id=video_id,
            )
        _live_chat_failures.pop(cache_key, None)
        return live_chat_id

    _live_chat_failures[cache_key] = _cache_now()
    return None


async def resolve_channel_metadata(
    slug: str,
    *,
    api_key: Optional[str],
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    if client is None:
        async with httpx.AsyncClient(timeout=10) as new_client:
            return await _resolve_channel_metadata_with_client(
                new_client, slug, api_key
            )
    return await _resolve_channel_metadata_with_client(
        client, slug, api_key
    )


async def resolve_live_chat_id(
    channel_id: str,
    *,
    api_key: Optional[str],
    channel_slug: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[str]:
    if client is None:
        async with httpx.AsyncClient(timeout=10) as new_client:
            return await _resolve_live_chat_id_with_client(
                new_client,
                channel_id,
                api_key,
                channel_slug=channel_slug,
            )
    return await _resolve_live_chat_id_with_client(
        client,
        channel_id,
        api_key,
        channel_slug=channel_slug,
    )


def normalise_channel_slug(value: str) -> str:
    slug = (value or "").strip()
    if not slug:
        return ""

    slug = slug.replace("https://www.youtube.com/", "")
    slug = slug.replace("http://www.youtube.com/", "")
    slug = slug.replace("https://youtube.com/", "")
    slug = slug.replace("http://youtube.com/", "")
    slug = slug.replace("www.youtube.com/", "")
    slug = slug.replace("youtube.com/", "")
    slug = slug.replace("channel/", "")
    slug = slug.replace("c/", "")
    slug = slug.replace("user/", "")

    slug = slug.split("?", 1)[0].split("#", 1)[0]
    if "/" in slug:
        slug = slug.split("/", 1)[0]

    slug = slug.strip()
    if not slug.startswith("@"):
        return ""
    return slug.lower()


class YouTubeChatClient:
    """Poll-based YouTube Live Chat reader."""

    def __init__(self, channel: str, queue: "asyncio.Queue[dict]", stop_event: asyncio.Event) -> None:
        self.channel_input = channel
        self.channel = normalise_channel_slug(channel)
        self.queue = queue
        self.stop_event = stop_event
        self._api_key = settings.youtube_api_key
        self._channel_id: Optional[str] = None
        self._channel_title: Optional[str] = None
        self._channel_thumbnail: Optional[str] = None
        self._live_chat_id: Optional[str] = None
        self._seen_message_ids: set[str] = set()

    async def ensure_channel_exists(self) -> None:
        if not self._api_key:
            raise RuntimeError("YouTube API key not configured")

        async with httpx.AsyncClient(timeout=10) as client:
            channel_id, title, thumbnail = await resolve_channel_metadata(
                self.channel,
                api_key=self._api_key,
                client=client,
            )
            if not channel_id:
                raise RuntimeError(f"YouTube channel '{self.channel}' not found")
            self._channel_id = channel_id
            self._channel_title = title or self.channel
            self._channel_thumbnail = thumbnail or None

            live_chat_id = await resolve_live_chat_id(
                channel_id,
                api_key=self._api_key,
                channel_slug=self.channel,
                client=client,
            )
            if not live_chat_id:
                raise RuntimeError(f"No active YouTube live chat found for '{self._channel_title}'")
            self._live_chat_id = live_chat_id
            logger.info("Connecting to YouTube live chat for %s", self.channel)

    async def run(self) -> None:
        if not self._live_chat_id:
            try:
                await self.ensure_channel_exists()
            except Exception as exc:
                await self.queue.put(
                    {
                        "platform": "youtube",
                        "channel": self.channel,
                        "type": "error",
                        "message": f"YouTube channel lookup failed: {exc}",
                    }
                )
                return

        if not self._live_chat_id or not self._api_key:
            await self.queue.put(
                {
                    "platform": "youtube",
                    "channel": self.channel,
                    "type": "error",
                    "message": "YouTube live chat unavailable",
                }
            )
            return

        await self.queue.put(
            {
                "platform": "youtube",
                "channel": self.channel,
                "type": "status",
                "message": f"Connected to YouTube chat for {self._channel_title or self.channel}",
            }
        )

        next_page_token: Optional[str] = None
        poll_interval = 2.5

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                while not self.stop_event.is_set():
                    params = {
                        "part": "snippet,authorDetails",
                        "liveChatId": self._live_chat_id,
                        "maxResults": 200,
                        "key": self._api_key,
                    }
                    if next_page_token:
                        params["pageToken"] = next_page_token

                    try:
                        response = await client.get(LIVE_CHAT_MESSAGES_ENDPOINT, params=params)
                    except httpx.HTTPError as exc:
                        logger.warning("YouTube chat request failed: %s", exc)
                        await asyncio.sleep(poll_interval)
                        continue

                    if response.status_code == 403:
                        await self.queue.put(
                            {
                                "platform": "youtube",
                                "channel": self.channel,
                                "type": "error",
                                "message": "YouTube API quota exceeded or access denied",
                            }
                        )
                        break
                    if response.status_code in {404, 410}:
                        invalidate_live_chat_cache(self._channel_id, self.channel)
                        self._live_chat_id = None
                        await self.queue.put(
                            {
                                "platform": "youtube",
                                "channel": self.channel,
                                "type": "status",
                                "message": "YouTube live chat ended",
                            }
                        )
                        break
                    if response.status_code != 200:
                        logger.warning(
                            "YouTube chat request returned %s: %s",
                            response.status_code,
                            response.text,
                        )
                        await asyncio.sleep(poll_interval)
                        continue

                    payload = response.json()
                    items = payload.get("items") if isinstance(payload, dict) else None
                    if isinstance(items, list):
                        for item in items:
                            message_payload = self._parse_message(item)
                            if message_payload:
                                await self.queue.put(message_payload)

                    next_page_token = payload.get("nextPageToken") if isinstance(payload, dict) else None
                    interval_ms = (
                        payload.get("pollingIntervalMillis") if isinstance(payload, dict) else None
                    )
                    if isinstance(interval_ms, (int, float)) and interval_ms > 0:
                        poll_interval = max(1.0, interval_ms / 1000)
                    await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            raise
        finally:
            if self._channel_id and not self._live_chat_id:
                invalidate_live_chat_cache(self._channel_id, self.channel)
            logger.info("Disconnected from YouTube live chat for %s", self.channel)
            await self.queue.put(
                {
                    "platform": "youtube",
                    "channel": self.channel,
                    "type": "status",
                    "message": f"Disconnected from YouTube chat for {self._channel_title or self.channel}",
                }
            )

    def _parse_message(self, item: Any) -> Optional[dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        message_id = item.get("id")
        if isinstance(message_id, str):
            if message_id in self._seen_message_ids:
                return None
            self._seen_message_ids.add(message_id)
            if len(self._seen_message_ids) > 5000:
                try:
                    self._seen_message_ids.pop()
                except KeyError:  # pragma: no cover - defensive
                    pass

        snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else None
        author = item.get("authorDetails") if isinstance(item.get("authorDetails"), dict) else None
        if not snippet or not author:
            return None

        message_type = snippet.get("type")
        if message_type not in {"textMessageEvent", "superChatEvent"}:
            return None

        display_message = snippet.get("displayMessage")
        if message_type == "superChatEvent":
            details = snippet.get("superChatDetails")
            if isinstance(details, dict):
                user_comment = details.get("userComment")
                if isinstance(user_comment, str) and user_comment.strip():
                    display_message = user_comment

        if not isinstance(display_message, str) or not display_message.strip():
            return None

        author_name = author.get("displayName")
        if not isinstance(author_name, str):
            author_name = "YouTube User"

        payload: dict[str, Any] = {
            "platform": "youtube",
            "type": "chat",
            "user": author_name,
            "message": display_message,
            "channel": self.channel,
        }

        author_channel_id = author.get("channelId")
        if isinstance(author_channel_id, str):
            payload["user_id"] = author_channel_id

        profile_image = author.get("profileImageUrl")
        if isinstance(profile_image, str) and profile_image:
            payload["avatar"] = profile_image

        badges = []
        raw_badges = author.get("badges")
        if isinstance(raw_badges, list):
            for badge in raw_badges:
                if not isinstance(badge, dict):
                    continue
                title = badge.get("title")
                icon_url = badge.get("iconUrl")
                if not isinstance(title, str):
                    continue
                badge_payload = {"title": title, "set_id": "youtube", "version": title.lower()}
                if isinstance(icon_url, str) and icon_url:
                    badge_payload["image_url"] = icon_url
                badges.append(badge_payload)
        else:
            special_flags = {
                "isChatOwner": "Owner",
                "isChatModerator": "Moderator",
                "isChatSponsor": "Sponsor",
                "isVerified": "Verified",
            }
            for field, title in special_flags.items():
                if author.get(field):
                    badges.append({"title": title, "set_id": "youtube", "version": title.lower()})
        if badges:
            payload["badges"] = badges

        if isinstance(message_id, str):
            payload["id"] = message_id

        if self._channel_thumbnail:
            payload["channel_profile_image"] = self._channel_thumbnail
        if self._channel_title:
            payload.setdefault("channel_display_name", self._channel_title)

        return payload

    @staticmethod
    def _extract_channel_metadata(entry: dict[str, Any]) -> tuple[Optional[str], Optional[str], Optional[str]]:
        channel_id = entry.get("id")
        snippet = entry.get("snippet") if isinstance(entry.get("snippet"), dict) else None
        title = snippet.get("title") if isinstance(snippet, dict) else None
        thumbnail = None
        if isinstance(snippet, dict):
            thumbnails = snippet.get("thumbnails")
            if isinstance(thumbnails, dict):
                for key in ("high", "medium", "default"):
                    candidate = thumbnails.get(key)
                    if isinstance(candidate, dict):
                        url = candidate.get("url")
                        if isinstance(url, str) and url:
                            thumbnail = url
                            break
        return (
            str(channel_id) if channel_id else None,
            title if isinstance(title, str) else None,
            thumbnail,
        )


async def fetch_live_chat_id_for_channel(
    client: httpx.AsyncClient,
    *,
    api_key: str,
    channel_id: str,
    channel_slug: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """Return the active live chat id and source video id for the specified channel."""

    video_id: Optional[str] = None

    primary_search_params = {
        "part": "id",
        "channelId": channel_id,
        "eventType": "live",
        "type": "video",
        "order": "viewCount",
        "maxResults": 1,
        "key": api_key,
    }
    response = await client.get(SEARCH_ENDPOINT, params=primary_search_params)
    if response.status_code == 200:
        payload = response.json()
        items = payload.get("items") if isinstance(payload, dict) else None
        if isinstance(items, list) and items:
            entry = items[0]
            if isinstance(entry, dict):
                item_id = entry.get("id")
                if isinstance(item_id, dict):
                    candidate = item_id.get("videoId")
                    if isinstance(candidate, str) and candidate:
                        video_id = candidate

    if not video_id and channel_slug:
        secondary_params = {
            "part": "id",
            "eventType": "live",
            "type": "video",
            "maxResults": 1,
            "key": api_key,
            "q": channel_slug,
        }
        response = await client.get(SEARCH_ENDPOINT, params=secondary_params)
        if response.status_code == 200:
            payload = response.json()
            items = payload.get("items") if isinstance(payload, dict) else None
            if isinstance(items, list) and items:
                entry = items[0]
                if isinstance(entry, dict):
                    item_id = entry.get("id")
                    if isinstance(item_id, dict):
                        candidate = item_id.get("videoId")
                        if isinstance(candidate, str) and candidate:
                            video_id = candidate

    if not video_id:
        return None, None

    video_params = {
        "part": "liveStreamingDetails",
        "id": video_id,
        "key": api_key,
        "maxResults": 1,
    }
    response = await client.get(VIDEOS_ENDPOINT, params=video_params)
    if response.status_code != 200:
        logger.debug(
            "YouTube videos lookup failed for %s (%s): %s",
            video_id,
            response.status_code,
            response.text,
        )
        return None, None

    payload = response.json()
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        return None, None
    entry = items[0]
    if not isinstance(entry, dict):
        return None
    details = entry.get("liveStreamingDetails")
    if not isinstance(details, dict):
        return None, None

    live_chat_id = details.get("activeLiveChatId")
    if isinstance(live_chat_id, str) and live_chat_id:
        return live_chat_id, video_id
    return None, None
