import asyncio
import logging
from typing import Any, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
LIVE_CHAT_MESSAGES_ENDPOINT = f"{YOUTUBE_API_BASE}/liveChat/messages"
LIVE_BROADCASTS_ENDPOINT = f"{YOUTUBE_API_BASE}/liveBroadcasts"
CHANNELS_ENDPOINT = f"{YOUTUBE_API_BASE}/channels"
SEARCH_ENDPOINT = f"{YOUTUBE_API_BASE}/search"
VIDEOS_ENDPOINT = f"{YOUTUBE_API_BASE}/videos"


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

    if slug.startswith("@"):
        slug = slug.lower()
    elif slug.startswith("UC"):
        slug = slug.strip()
    else:
        slug = slug.replace("/", "")
        slug = slug.replace(" ", "")
        slug = slug.strip()
    return slug


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
            channel_id, title, thumbnail = await self._resolve_channel_metadata(client)
            if not channel_id:
                raise RuntimeError(f"YouTube channel '{self.channel}' not found")
            self._channel_id = channel_id
            self._channel_title = title or self.channel
            self._channel_thumbnail = thumbnail or None

            live_chat_id = await self._lookup_live_chat_id(client, channel_id)
            if not live_chat_id:
                raise RuntimeError(f"No active YouTube live chat found for '{self._channel_title}'")
            self._live_chat_id = live_chat_id

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
                        poll_interval = max(1.0, min(10.0, interval_ms / 1000))
                    await asyncio.sleep(poll_interval)
        except asyncio.CancelledError:
            raise
        finally:
            await self.queue.put(
                {
                    "platform": "youtube",
                    "channel": self.channel,
                    "type": "status",
                    "message": f"Disconnected from YouTube chat for {self._channel_title or self.channel}",
                }
            )

    async def _resolve_channel_metadata(
        self,
        client: httpx.AsyncClient,
    ) -> tuple[Optional[str], Optional[str], Optional[str]]:
        slug = self.channel
        attempts: list[dict[str, Any]] = []

        if slug.startswith("UC") and len(slug) >= 10:
            attempts.append({"id": slug})
        if slug.startswith("@"):
            attempts.append({"forHandle": slug.lstrip("@")})
        attempts.append({"forUsername": slug})

        params_base = {
            "part": "id,snippet",
            "maxResults": 1,
            "key": self._api_key,
        }

        for attempt in attempts:
            params = {**params_base, **attempt}
            response = await client.get(CHANNELS_ENDPOINT, params=params)
            if response.status_code != 200:
                continue
            payload = response.json()
            items = payload.get("items") if isinstance(payload, dict) else None
            if isinstance(items, list) and items:
                entry = items[0] if isinstance(items[0], dict) else {}
                return self._extract_channel_metadata(entry)

        search_params = {
            "part": "snippet",
            "type": "channel",
            "maxResults": 1,
            "q": slug,
            "key": self._api_key,
        }
        response = await client.get(SEARCH_ENDPOINT, params=search_params)
        if response.status_code == 200:
            payload = response.json()
            items = payload.get("items") if isinstance(payload, dict) else None
            if isinstance(items, list) and items:
                entry = items[0] if isinstance(items[0], dict) else {}
                snippet = entry.get("snippet") if isinstance(entry, dict) else None
                channel_id = entry.get("snippet", {}).get("channelId") if isinstance(entry.get("snippet"), dict) else None
                if not channel_id:
                    channel_id = entry.get("id", {}).get("channelId") if isinstance(entry.get("id"), dict) else None
                title = snippet.get("channelTitle") if isinstance(snippet, dict) else None
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
                if channel_id:
                    return (
                        str(channel_id),
                        title if isinstance(title, str) else None,
                        thumbnail,
                    )

        return None, None, None

    async def _lookup_live_chat_id(
        self,
        client: httpx.AsyncClient,
        channel_id: str,
    ) -> Optional[str]:
        if not self._api_key:
            return None
        return await fetch_live_chat_id_for_channel(
            client,
            api_key=self._api_key,
            channel_id=channel_id,
            channel_slug=self.channel,
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
) -> Optional[str]:
    """Return active live chat id for the specified channel using API key lookups."""

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

    if not video_id:
        secondary_params = {
            "part": "id",
            "eventType": "live",
            "type": "video",
            "maxResults": 1,
            "key": api_key,
        }
        if channel_slug:
            secondary_params["q"] = channel_slug
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
        return None

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
        return None

    payload = response.json()
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        return None
    entry = items[0]
    if not isinstance(entry, dict):
        return None
    details = entry.get("liveStreamingDetails")
    if not isinstance(details, dict):
        return None

    live_chat_id = details.get("activeLiveChatId")
    if isinstance(live_chat_id, str) and live_chat_id:
        return live_chat_id
    return None
