import asyncio
import logging
import secrets
import time
from typing import Optional

import httpx
from app.config import settings

logger = logging.getLogger(__name__)


class TwitchChatClient:
    """Minimal IRC client that listens for Twitch chat messages."""

    HOST = "irc.chat.twitch.tv"
    PORT = 6667

    def __init__(self, channel: str, queue: "asyncio.Queue[dict]", stop_event: asyncio.Event) -> None:
        self.channel = channel.lower()
        self.queue = queue
        self.stop_event = stop_event
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._badge_cache: dict[tuple[str, str], dict[str, str]] = {}
        self._badge_token: Optional[str] = None
        self._badge_token_expires_at: float = 0.0

    async def run(self) -> None:
        nickname = f"justinfan{secrets.randbelow(1_000_000):06d}"
        try:
            await self._ensure_badge_cache()
            logger.info("Connecting to Twitch IRC for #%s", self.channel)
            self._reader, self._writer = await asyncio.open_connection(self.HOST, self.PORT)
            await self._write_line("CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership")
            await self._write_line("PASS SCHMOOPIIE")
            await self._write_line(f"NICK {nickname}")
            await self._write_line(f"JOIN #{self.channel}")
            await self.queue.put(
                {
                    "platform": "twitch",
                    "type": "status",
                    "message": f"Connected to Twitch chat for {self.channel}",
                }
            )

            while not self.stop_event.is_set():
                try:
                    raw = await asyncio.wait_for(self._reader.readline(), timeout=300)
                except asyncio.TimeoutError:
                    await self._write_line("PING :keepalive")
                    continue

                if not raw:
                    break

                decoded = raw.decode(errors="ignore").strip()
                if decoded.startswith("PING"):
                    await self._write_line(decoded.replace("PING", "PONG"))
                    continue

                if "PRIVMSG" in decoded:
                    message_payload = self._parse_privmsg(decoded)
                    if message_payload:
                        await self.queue.put(message_payload)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Twitch client failure: %s", exc)
            await self.queue.put(
                {
                    "platform": "twitch",
                    "type": "error",
                    "message": f"Twitch connection failed: {exc}",
                }
            )
        finally:
            if self._writer:
                self._writer.close()
                try:
                    await self._writer.wait_closed()
                except Exception:  # pragma: no cover - best effort cleanup
                    pass
            await self.queue.put(
                {
                    "platform": "twitch",
                    "type": "status",
                    "message": f"Disconnected from Twitch chat for {self.channel}",
                }
            )

    async def ensure_channel_exists(self) -> None:
        client_id = settings.twitch_client_id
        client_secret = settings.twitch_client_secret
        if not client_id or not client_secret:
            return
        token = await self._get_app_access_token(client_id, client_secret)
        broadcaster_id = await self._lookup_broadcaster_id(token, client_id)
        if not broadcaster_id:
            raise RuntimeError(f"Twitch channel '{self.channel}' not found")

    @staticmethod
    def _unescape_tag_value(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        replacements = {
            r"\s": " ",
            r"\n": "\n",
            r"\r": "\r",
            r"\:": ";",
            r"\\": "\\",
        }
        result = value
        for pattern, replacement in replacements.items():
            result = result.replace(pattern, replacement)
        return result

    async def _write_line(self, message: str) -> None:
        if not self._writer:
            raise RuntimeError("Twitch writer not initialised")
        self._writer.write((message + "\r\n").encode())
        await self._writer.drain()

    def _parse_privmsg(self, payload: str) -> Optional[dict]:
        tags: dict[str, str] = {}
        if payload.startswith("@"):
            tags_chunk, _, payload = payload.partition(" ")
            tags = self._parse_tags(tags_chunk[1:])

        try:
            prefix, _, remainder = payload.partition(" PRIVMSG ")
            username = prefix.split("!")[0][1:]
            display_name = self._unescape_tag_value(tags.get("display-name")) or username
            _, _, text = remainder.partition(" :")
            message_payload = {
                "platform": "twitch",
                "type": "chat",
                "user": display_name,
                "message": text,
            }

            message_id = tags.get("id")
            if message_id:
                message_payload["id"] = message_id

            reply_parent_id = tags.get("reply-parent-msg-id")
            if reply_parent_id:
                parent_display = self._unescape_tag_value(
                    tags.get("reply-parent-display-name")
                ) or tags.get("reply-parent-user-login")
                parent_message = self._unescape_tag_value(tags.get("reply-parent-msg-body"))
                parent_user_id = tags.get("reply-parent-user-id")
                message_payload["reply"] = {
                    "message_id": reply_parent_id,
                    "user": parent_display or "",
                    "user_id": parent_user_id or "",
                    "message": parent_message or "",
                }

            color = tags.get("color")
            if color:
                message_payload["color"] = color

            user_id = tags.get("user-id")
            if user_id:
                message_payload["user_id"] = user_id

            emotes = self._parse_emotes(tags.get("emotes"), text)
            if emotes:
                message_payload["emotes"] = emotes

            badges = self._resolve_badges(tags.get("badges"))
            if badges:
                message_payload["badges"] = badges

            return message_payload
        except (IndexError, ValueError):
            logger.debug("Unable to parse Twitch PRIVMSG: %s", payload)
            return None

    def _parse_tags(self, chunk: str) -> dict[str, str]:
        tags: dict[str, str] = {}
        for item in chunk.split(";"):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            tags[key] = value
        return tags

    def _parse_emotes(self, emote_tag: Optional[str], message: str) -> list[dict[str, object]]:
        if not emote_tag:
            return []

        emote_map: dict[tuple[str, str], list[tuple[int, int]]] = {}
        for entry in emote_tag.split("/"):
            emote_id, _, positions = entry.partition(":")
            if not emote_id or not positions:
                continue
            slices: list[tuple[int, int]] = []
            for span in positions.split(","):
                start_str, _, end_str = span.partition("-")
                if not start_str or not end_str:
                    continue
                try:
                    start, end = int(start_str), int(end_str)
                except ValueError:
                    continue
                if start < 0 or end < start or end >= len(message):
                    continue
                slices.append((start, end))
            if not slices:
                continue
            name = message[slices[0][0] : slices[0][1] + 1]
            key = (emote_id, name)
            emote_map.setdefault(key, []).extend(slices)

        return [
            {"id": emote_id, "name": name, "positions": positions}
            for (emote_id, name), positions in emote_map.items()
        ]

    async def _ensure_badge_cache(self) -> None:
        if self._badge_cache:
            return

        client_id = settings.twitch_client_id
        client_secret = settings.twitch_client_secret

        if not client_id or not client_secret:
            logger.info("Skipping Twitch badge lookup; Twitch credentials not configured")
            return

        try:
            token = await self._get_app_access_token(client_id, client_secret)
            self._badge_cache.update(await self._fetch_badges(token, client_id, None))
            broadcaster_id = await self._lookup_broadcaster_id(token, client_id)
            if broadcaster_id:
                self._badge_cache.update(
                    await self._fetch_badges(token, client_id, broadcaster_id)
                )
        except Exception:  # pragma: no cover - network heavy
            logger.exception("Failed to load Twitch badges for #%s", self.channel)
            self._badge_cache.clear()

    async def _get_app_access_token(self, client_id: str, client_secret: str) -> str:
        if (
            self._badge_token
            and self._badge_token_expires_at
            and self._badge_token_expires_at - time.time() > 60
        ):
            return self._badge_token

        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post("https://id.twitch.tv/oauth2/token", data=payload)

        if response.status_code != 200:
            raise RuntimeError(
                f"Unable to obtain Twitch app access token (status {response.status_code})"
            )

        data = response.json()
        token = data.get("access_token")
        expires_in = data.get("expires_in")
        if not token or not isinstance(token, str):
            raise RuntimeError("Twitch token response missing access_token")
        if isinstance(expires_in, int):
            self._badge_token_expires_at = time.time() + max(0, expires_in)
        else:
            self._badge_token_expires_at = time.time() + 3600
        self._badge_token = token
        return token

    async def _lookup_broadcaster_id(self, token: str, client_id: str) -> Optional[str]:
        params = {"login": self.channel}
        headers = {
            "Authorization": f"Bearer {token}",
            "Client-Id": client_id,
        }
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get("https://api.twitch.tv/helix/users", params=params, headers=headers)

        if response.status_code != 200:
            logger.debug(
                "Failed to resolve Twitch broadcaster %s (status %s)",
                self.channel,
                response.status_code,
            )
            return None

        try:
            data = response.json()
        except ValueError:
            logger.debug("Invalid JSON while resolving Twitch broadcaster for %s", self.channel)
            return None

        entries = data.get("data") if isinstance(data, dict) else None
        if isinstance(entries, list) and entries:
            broadcaster_id = entries[0].get("id")
            if broadcaster_id:
                return str(broadcaster_id)
        return None

    async def _fetch_badges(
        self, token: str, client_id: str, broadcaster_id: Optional[str]
    ) -> dict[tuple[str, str], dict[str, str]]:
        if broadcaster_id:
            url = "https://api.twitch.tv/helix/chat/badges"
            params = {"broadcaster_id": broadcaster_id}
        else:
            url = "https://api.twitch.tv/helix/chat/badges/global"
            params = None

        headers = {
            "Authorization": f"Bearer {token}",
            "Client-Id": client_id,
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, params=params, headers=headers)

        if response.status_code != 200:
            raise RuntimeError(f"Badge lookup failed with status {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("Badge lookup returned invalid JSON") from exc

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return {}

        badges: dict[tuple[str, str], dict[str, str]] = {}
        for badge_set in data:
            set_id = badge_set.get("set_id") if isinstance(badge_set, dict) else None
            versions = badge_set.get("versions") if isinstance(badge_set, dict) else None
            if not set_id or not isinstance(versions, list):
                continue
            for version in versions:
                if not isinstance(version, dict):
                    continue
                version_id = version.get("id")
                if not version_id:
                    continue
                image_url = (
                    version.get("image_url_1x")
                    or version.get("image_url_2x")
                    or version.get("image_url_4x")
                )
                if not image_url:
                    continue
                title = version.get("title") or version.get("description") or set_id
                badges[(set_id, version_id)] = {
                    "set_id": set_id,
                    "version": version_id,
                    "title": str(title),
                    "image_url": str(image_url),
                }
        return badges

    def _resolve_badges(self, badges_tag: Optional[str]) -> list[dict[str, str]]:
        if not badges_tag or not self._badge_cache:
            return []

        result: list[dict[str, str]] = []
        for token in badges_tag.split(","):
            if not token:
                continue
            set_id, _, version = token.partition("/")
            if not set_id or not version:
                continue
            badge = self._badge_cache.get((set_id, version))
            if badge:
                result.append(badge)
        return result
