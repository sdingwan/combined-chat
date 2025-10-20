import asyncio
import json
import logging
from typing import Optional

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

from pathlib import Path

from app.config import settings

GLOBAL_BADGE_FILENAMES: dict[str, str] = {
    "verified": "verified",
    "moderator": "moderator",
    "vip": "vip",
    "staff": "staff",
    "bot": "bot",
    "founder": "founder",
    "partner": "partner",
    "broadcaster": "broadcaster",
    "og": "og",
}

GLOBAL_BADGE_TITLES: dict[str, str] = {
    "verified": "Verified channel",
    "moderator": "Moderator",
    "vip": "VIP",
    "staff": "Staff",
    "bot": "Bot",
    "founder": "Founder",
    "partner": "Partner",
    "broadcaster": "Broadcaster",
    "og": "OG Supporter",
}

STATIC_BADGE_WEB_BASE = "/static/badges/kick"
STATIC_BADGE_DIR = Path(__file__).resolve().parents[2] / "static" / "badges" / "kick"
STATIC_BADGE_DIR.mkdir(parents=True, exist_ok=True)
BADGE_EXTENSION_PREFERENCE = ("svg", "png", "webp", "gif", "jpg", "jpeg")
SUBSCRIBER_FALLBACK_NAME = "subscriber"
SUBSCRIBER_FALLBACK_TITLE = "Subscriber"


class KickBadgeResolver:
    """Resolve Kick identity badge metadata to renderable assets."""

    CHANNEL_ENDPOINT = "https://kick.com/api/v2/channels/{slug}"

    def __init__(self, channel: str) -> None:
        self.channel = channel.replace("_", "-")
        self._subscriber_badges: dict[int, dict[str, str]] = {}
        self._fetch_attempted = False
        self._logger = logging.getLogger(__name__)
        self._badge_dir = STATIC_BADGE_DIR
        self._missing_global_badges: set[str] = set()

    async def resolve(self, badges: list[dict]) -> list[dict[str, str]]:
        """Translate raw Kick badge descriptors into frontend-friendly data."""

        if not badges:
            return []

        resolved: list[dict[str, str]] = []
        for badge in badges:
            badge_type = str(badge.get("type") or "").lower()
            if not badge_type:
                continue

            if badge_type.startswith("subscriber") or badge_type == "sub":
                badge_type = "subscriber"

            if badge_type == "subscriber":
                enriched = await self._resolve_subscriber_badge(badge)
                if enriched:
                    resolved.append(enriched)
                continue
            enriched = self._resolve_global_badge(badge_type, badge)
            if enriched:
                resolved.append(enriched)

        return resolved

    def _compose_payload(
        self,
        set_id: str,
        badge: dict,
        image_url: str,
        default_title: Optional[str] = None,
        version: Optional[str] = None,
    ) -> dict[str, str]:
        payload: dict[str, str] = {
            "image_url": image_url,
            "set_id": set_id,
        }
        if version is not None:
            payload["version"] = version
        title = badge.get("text") or default_title
        if title:
            payload["title"] = str(title)
        return payload

    def _extract_badge_image_url(self, badge: dict) -> Optional[str]:
        """Return an image URL embedded in the raw badge payload."""

        image_url = badge.get("image_url")
        if isinstance(image_url, str) and image_url:
            return image_url

        image = badge.get("badge_image") or badge.get("image")
        if isinstance(image, dict):
            candidate = image.get("src") or image.get("url")
            if isinstance(candidate, str) and candidate:
                return candidate
        elif isinstance(image, str) and image:
            return image
        return None

    async def _resolve_subscriber_badge(self, badge: dict) -> Optional[dict[str, str]]:
        months_raw = (
            badge.get("count")
            or badge.get("months")
            or badge.get("quantity")
            or badge.get("tier")
            or badge.get("level")
        )
        try:
            months = int(months_raw) if months_raw is not None else None
        except (TypeError, ValueError):
            months = None

        await self._ensure_channel_badges()

        asset: Optional[dict[str, str]] = None
        version: Optional[str] = None
        asset_months: Optional[int] = months

        if months is not None:
            asset = self._subscriber_badges.get(months)
            if not asset and self._subscriber_badges:
                eligible = [key for key in self._subscriber_badges if key <= months]
                if eligible:
                    asset_months = max(eligible)
                    asset = self._subscriber_badges.get(asset_months)
                else:
                    eligible = [key for key in self._subscriber_badges if key >= months]
                    if eligible:
                        asset_months = min(eligible)
                        asset = self._subscriber_badges.get(asset_months)
            if asset and asset_months is not None:
                version = str(asset_months)

        if not asset:
            direct_url = self._extract_badge_image_url(badge)
            if direct_url:
                asset = {
                    "image_url": direct_url,
                    "title": badge.get("text") or "Subscriber",
                }
                if months is not None:
                    version = str(months)

        if not asset:
            fallback_file = self._find_badge_file(SUBSCRIBER_FALLBACK_NAME)
            if fallback_file:
                asset = {
                    "image_url": f"{STATIC_BADGE_WEB_BASE}/{fallback_file.name}",
                    "title": SUBSCRIBER_FALLBACK_TITLE,
                }
                if months is not None:
                    version = str(months)

        if not asset:
            return None

        payload = self._compose_payload(
            "subscriber",
            badge,
            asset["image_url"],
            asset.get("title"),
            version,
        )
        if months is not None:
            title_source = badge.get("text") or asset.get("title")
            if title_source:
                payload["title"] = f"{title_source} ({months} months)"
        elif not payload.get("title"):
            title = asset.get("title")
            if title:
                payload["title"] = title
        return payload

    def _resolve_global_badge(self, badge_type: str, badge: dict) -> Optional[dict[str, str]]:
        default_title = GLOBAL_BADGE_TITLES.get(badge_type)
        inline_image = self._extract_badge_image_url(badge)
        if inline_image:
            return self._compose_payload(badge_type, badge, inline_image, default_title)

        image_url = self._build_global_badge_url(badge_type)
        if not image_url:
            return None
        return self._compose_payload(badge_type, badge, image_url, default_title)

    def _build_global_badge_url(self, badge_type: str) -> Optional[str]:
        filename = GLOBAL_BADGE_FILENAMES.get(badge_type)
        if not filename:
            return None

        badge_file = self._find_badge_file(filename)
        if not badge_file:
            if badge_type not in self._missing_global_badges:
                self._logger.debug(
                    "Kick badge asset '%s' not found under %s", badge_type, self._badge_dir
                )
                self._missing_global_badges.add(badge_type)
            return None

        return f"{STATIC_BADGE_WEB_BASE}/{badge_file.name}"

    def _find_badge_file(self, base_name: str) -> Optional[Path]:
        if not self._badge_dir or not self._badge_dir.exists():
            return None

        if "." in base_name:
            candidate = self._badge_dir / base_name
            if candidate.exists():
                return candidate

        for ext in BADGE_EXTENSION_PREFERENCE:
            candidate = self._badge_dir / f"{base_name}.{ext}"
            if candidate.exists():
                return candidate

        matches = list(self._badge_dir.glob(f"{base_name}.*"))
        return matches[0] if matches else None

    async def _ensure_channel_badges(self) -> None:
        if self._subscriber_badges or self._fetch_attempted:
            return

        self._fetch_attempted = True

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5_0) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
            "Referer": f"https://kick.com/{self.channel}",
            "Origin": "https://kick.com",
            "Accept": "application/json",
        }

        token = getattr(settings, "kick_client_token", None)
        if token:
            headers["X-CLIENT-TOKEN"] = token

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(
                    self.CHANNEL_ENDPOINT.format(slug=self.channel), headers=headers
                )
            response.raise_for_status()
        except Exception as exc:  # pragma: no cover - network heavy
            self._logger.warning("Failed to fetch Kick channel badge metadata: %s", exc)
            return

        try:
            data = response.json()
        except ValueError:  # pragma: no cover - network heavy
            self._logger.warning("Kick channel badge response was not valid JSON for %s", self.channel)
            return
        subscriber_badges = data.get("subscriber_badges")
        if not isinstance(subscriber_badges, list):
            return

        for badge in subscriber_badges:
            months = badge.get("months")
            image = badge.get("badge_image")
            image_url = image.get("src") if isinstance(image, dict) else None
            if months is None or not image_url:
                continue
            try:
                months_key = int(months)
            except (TypeError, ValueError):
                continue
            self._subscriber_badges[months_key] = {
                "image_url": image_url,
                "title": "Subscriber",
            }

logger = logging.getLogger(__name__)


class KickChatClient:
    """Pusher-based websocket client that streams Kick chat messages."""

    API_BASE = "https://kick.com/api/v2"
    APP_KEY = "32cbd69e4b950bf97679"
    CLUSTER = "us2"
    WS_URL_TEMPLATE = (
        "wss://ws-{cluster}.pusher.com/app/{key}?protocol=7&client=js&version=7.6.0&flash=false"
    )
    CHANNEL_TEMPLATE = "chatrooms.{chatroom_id}.v2"
    PONG_PAYLOAD = json.dumps({"event": "pusher:pong", "data": {}})

    def __init__(self, channel: str, queue: "asyncio.Queue[dict]", stop_event: asyncio.Event) -> None:
        self.channel = channel.lower()
        self.queue = queue
        self.stop_event = stop_event
        self._badge_resolver = KickBadgeResolver(self.channel)
        self._chatroom_id: Optional[int] = None
        self._channel_profile_image: Optional[str] = None
        self._channel_display_name: Optional[str] = None

    async def run(self) -> None:
        try:
            chatroom_id = self._chatroom_id or await self._fetch_chatroom_id()
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Failed to resolve Kick channel %s: %s", self.channel, exc)
            await self.queue.put(
                {
                    "platform": "kick",
                    "channel": self.channel,
                    "type": "error",
                    "message": f"Kick channel lookup failed: {exc}",
                }
            )
            await self.queue.put(
                {
                    "platform": "kick",
                    "channel": self.channel,
                    "type": "status",
                    "message": f"Stopped listening to {self.channel}",
                }
            )
            return

        try:
            await self._ensure_channel_profile()
        except Exception:  # pragma: no cover - defensive
            logger.debug("Failed to resolve Kick channel profile image for %s", self.channel, exc_info=True)

        ws_url = self.WS_URL_TEMPLATE.format(cluster=self.CLUSTER, key=self.APP_KEY)
        channel_name = self.CHANNEL_TEMPLATE.format(chatroom_id=chatroom_id)

        try:
            logger.info("Connecting to Kick Pusher for %s", self.channel)
            async with websockets.connect(ws_url, ping_interval=None, ping_timeout=None) as socket:
                await self._consume(socket, channel_name)
        except asyncio.CancelledError:
            raise
        except ConnectionClosed as exc:  # pragma: no cover - network heavy
            logger.info("Kick websocket closed: %s", exc)
        except Exception as exc:  # pragma: no cover - network heavy
            logger.exception("Kick websocket failure: %s", exc)
            await self.queue.put(
                {
                    "platform": "kick",
                    "channel": self.channel,
                    "type": "error",
                    "message": f"Kick connection failed: {exc}",
                }
            )
        finally:
            await self.queue.put(
                {
                    "platform": "kick",
                    "channel": self.channel,
                    "type": "status",
                    "message": f"Disconnected from Kick chat for {self.channel}",
                }
            )

    async def ensure_channel_exists(self) -> None:
        await self._fetch_chatroom_id()

    async def _consume(self, socket: websockets.WebSocketClientProtocol, channel_name: str) -> None:
        subscribed = False
        while not self.stop_event.is_set():
            try:
                raw = await asyncio.wait_for(socket.recv(), timeout=10)
            except asyncio.TimeoutError:
                continue

            message = self._decode_pusher_payload(raw)
            if not message:
                continue

            event = message.get("event")
            if event == "pusher:connection_established":
                subscribe_payload = {
                    "event": "pusher:subscribe",
                    "data": {"auth": "", "channel": channel_name},
                }
                await socket.send(json.dumps(subscribe_payload))
            elif event == "pusher_internal:subscription_succeeded":
                if not subscribed:
                    subscribed = True
                    await self.queue.put(
                        {
                            "platform": "kick",
                            "channel": self.channel,
                            "type": "status",
                            "message": f"Connected to Kick chat for {self.channel}",
                        }
                    )
            elif event == "pusher:ping":
                await socket.send(self.PONG_PAYLOAD)
            elif event == "App\\Events\\ChatMessageEvent":
                payload = await self._parse_chat_message(message)
                if payload:
                    await self.queue.put(payload)
            elif event == "pusher:error":
                error_data = message.get("data") or {}
                await self.queue.put(
                    {
                        "platform": "kick",
                        "channel": self.channel,
                        "type": "error",
                        "message": f"Kick reported error: {error_data}",
                    }
                )

    async def _fetch_chatroom_id(self) -> int:
        streamer = self.channel.replace("_", "-")
        params = httpx.QueryParams({"streamer": streamer})
        endpoint = f"https://api.stream-stuff.com/kickchatroomid.php?{params}"

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(endpoint, headers={"Accept": "application/json"})

        if response.status_code == 404:
            raise RuntimeError(f"Kick channel '{streamer}' not found")
        if response.status_code != 200:
            raise RuntimeError(
                f"Kick chatroom lookup returned status {response.status_code} for '{streamer}'"
            )

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError("Kick chatroom lookup response was not valid JSON") from exc

        chatroom_id = payload.get("chatroom_id") if isinstance(payload, dict) else None
        if not chatroom_id:
            raise RuntimeError("Kick chatroom lookup response missing chatroom id")

        chatroom_id_int = int(chatroom_id)
        self._chatroom_id = chatroom_id_int
        return chatroom_id_int

    async def _ensure_channel_profile(self) -> None:
        if self._channel_profile_image is not None:
            return

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5_0) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
            "Referer": f"https://kick.com/{self.channel}",
            "Origin": "https://kick.com",
            "Accept": "application/json",
        }

        token = getattr(settings, "kick_client_token", None)
        if token:
            headers["X-CLIENT-TOKEN"] = token

        url = f"{self.API_BASE}/channels/{self.channel.replace('_', '-')}"

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url, headers=headers)
            response.raise_for_status()
        except Exception:  # pragma: no cover - network heavy
            logger.debug("Failed to load Kick channel profile for %s", self.channel, exc_info=True)
            self._channel_profile_image = ""
            return

        try:
            data = response.json()
        except ValueError:  # pragma: no cover - network heavy
            logger.debug("Kick channel profile response invalid for %s", self.channel)
            self._channel_profile_image = ""
            return

        image_url = None
        user = data.get("user") if isinstance(data, dict) else None
        if isinstance(user, dict):
            image_candidate = (
                user.get("profile_pic")
                or user.get("profilePic")
                or user.get("profile_picture")
                or user.get("profilePicture")
            )
            if isinstance(image_candidate, str):
                image_url = image_candidate.strip() or None
            display_candidate = (
                user.get("display_name")
                or user.get("displayName")
                or user.get("username")
                or user.get("name")
            )
            if isinstance(display_candidate, str) and display_candidate.strip():
                self._channel_display_name = display_candidate.strip()
        self._channel_profile_image = image_url or ""
        if not self._channel_display_name:
            self._channel_display_name = self.channel

    def _decode_pusher_payload(self, raw: str) -> Optional[dict]:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Invalid JSON from Kick websocket: %s", raw)
            return None
    async def _parse_chat_message(self, message: dict) -> Optional[dict]:
        raw_data = message.get("data")
        if isinstance(raw_data, str):
            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                logger.debug("Unable to decode Kick chat payload: %s", raw_data)
                return None
        elif isinstance(raw_data, dict):
            data = raw_data
        else:
            return None

        content = data.get("content") or data.get("message")
        sender = data.get("sender") or data.get("user")
        user_id = None
        if isinstance(sender, dict):
            username = sender.get("username") or sender.get("slug")
            raw_id = sender.get("id") or sender.get("user_id")
            if raw_id is not None:
                user_id = str(raw_id)
        else:
            username = sender
            if sender and isinstance(data.get("sender"), dict):  # fallback if id was nested
                raw_id = data["sender"].get("id") or data["sender"].get("user_id")
                if raw_id is not None:
                    user_id = str(raw_id)

        if not content or not username:
            return None

        payload: dict[str, object] = {
            "platform": "kick",
            "type": "chat",
            "user": username,
            "message": content,
            "channel": self.channel,
        }

        if self._channel_profile_image:
            payload["channel_profile_image"] = self._channel_profile_image
        if self._channel_display_name:
            payload.setdefault("channel_display_name", self._channel_display_name)

        if isinstance(sender, dict):
            identity = sender.get("identity")
            if isinstance(identity, dict):
                color = identity.get("color")
                if color:
                    payload["color"] = color
                raw_badges = identity.get("badges")
                if isinstance(raw_badges, list):
                    resolved_badges = await self._badge_resolver.resolve(raw_badges)
                    if resolved_badges:
                        payload["badges"] = resolved_badges

        message_id = data.get("id") or data.get("chat_id") or data.get("uuid")
        if message_id is not None:
            payload["id"] = str(message_id)

        if user_id:
            payload["user_id"] = user_id

        metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}

        reply_source = data.get("reply_to") or data.get("replied_to") or metadata.get("reply_to")
        if isinstance(reply_source, dict):
            reply_msg = reply_source.get("message") or reply_source.get("content") or ""
            reply_user = reply_source.get("username") or reply_source.get("user") or ""
            reply_user_id = reply_source.get("user_id") or reply_source.get("id") or ""
            reply_message_id = (
                reply_source.get("id")
                or reply_source.get("message_id")
                or reply_source.get("chat_message_id")
            )
            payload["reply"] = {
                "message_id": str(reply_message_id) if reply_message_id is not None else "",
                "user": str(reply_user) if reply_user is not None else "",
                "user_id": str(reply_user_id) if reply_user_id is not None else "",
                "message": str(reply_msg) if reply_msg is not None else "",
            }

        if "reply" not in payload:
            original_sender = metadata.get("original_sender")
            original_message = metadata.get("original_message")
            if isinstance(original_sender, dict) or isinstance(original_message, dict):
                reply_user = ""
                reply_user_id = ""
                reply_msg = ""
                reply_message_id = ""

                if isinstance(original_sender, dict):
                    reply_user = (
                        original_sender.get("username")
                        or original_sender.get("user")
                        or original_sender.get("slug")
                        or ""
                    )
                    sender_id = original_sender.get("user_id") or original_sender.get("id")
                    if sender_id is not None:
                        reply_user_id = str(sender_id)

                if isinstance(original_message, dict):
                    reply_msg = (
                        original_message.get("content")
                        or original_message.get("message")
                        or original_message.get("text")
                        or ""
                    )
                    message_id = (
                        original_message.get("message_id")
                        or original_message.get("chat_message_id")
                        or original_message.get("id")
                    )
                    if message_id is not None:
                        reply_message_id = str(message_id)

                payload["reply"] = {
                    "message_id": reply_message_id,
                    "user": reply_user,
                    "user_id": reply_user_id,
                    "message": reply_msg,
                }

        return payload
