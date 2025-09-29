import asyncio
import logging
import secrets
from typing import Optional

import httpx

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
        self._badge_cache: dict[str, dict[str, dict[str, str]]] = {}
        self._badge_fetch_tasks: dict[str, asyncio.Task] = {}
        self._loaded_badge_urls: set[str] = set()
        self._channel_id: Optional[str] = None

    async def run(self) -> None:
        nickname = f"justinfan{secrets.randbelow(1_000_000):06d}"
        try:
            logger.info("Connecting to Twitch IRC for #%s", self.channel)
            self._reader, self._writer = await asyncio.open_connection(self.HOST, self.PORT)
            await self._write_line("CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership")
            await self._write_line("PASS SCHMOOPIIE")
            await self._write_line(f"NICK {nickname}")
            await self._write_line(f"JOIN #{self.channel}")
            await self._load_badge_manifest("https://badges.twitch.tv/v1/badges/global/display")
            await self.queue.put(
                {
                    "platform": "twitch",
                    "type": "status",
                    "message": f"Listening to Twitch chat for #{self.channel}",
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
                    "message": f"Stopped listening to #{self.channel}",
                }
            )

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

        room_id = tags.get("room-id")
        if room_id and room_id != self._channel_id:
            self._channel_id = room_id
            self._ensure_channel_badges(room_id)

        try:
            prefix, _, remainder = payload.partition(" PRIVMSG ")
            username = prefix.split("!")[0][1:]
            _, _, text = remainder.partition(" :")
            return {
                "platform": "twitch",
                "type": "chat",
                "user": username,
                "message": text,
                "badges": self._extract_badges(tags),
            }
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

    def _extract_badges(self, tags: dict[str, str]) -> list[dict[str, str]]:
        badges_raw = tags.get("badges")
        if not badges_raw:
            return []

        badges: list[dict[str, str]] = []
        for entry in badges_raw.split(","):
            badge_id, _, version = entry.partition("/")
            if not badge_id:
                continue
            badge_data = self._lookup_badge_metadata(badge_id, version)
            badge_payload: dict[str, str] = {"id": badge_id}
            if badge_data.get("label"):
                badge_payload["label"] = badge_data["label"]
            if badge_data.get("icon"):
                badge_payload["icon"] = badge_data["icon"]
            elif "label" not in badge_payload:
                badge_payload["label"] = badge_id.replace("_", " ").title()
            badges.append(badge_payload)
        return badges

    async def _load_badge_manifest(self, url: str) -> None:
        if url in self._loaded_badge_urls:
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url)
                response.raise_for_status()
            data = response.json()
        except Exception as exc:  # pragma: no cover - network heavy
            logger.debug("Failed to load badge manifest %s: %s", url, exc)
            return

        badge_sets = data.get("badge_sets", {})
        for set_id, set_data in badge_sets.items():
            versions = set_data.get("versions", {})
            cache = self._badge_cache.setdefault(set_id, {})
            for version_id, meta in versions.items():
                cache[version_id] = {
                    "label": meta.get("title") or meta.get("description") or set_id,
                    "icon": meta.get("image_url_1x")
                    or meta.get("image_url_2x")
                    or meta.get("image_url_4x"),
                }
        self._loaded_badge_urls.add(url)

    def _ensure_channel_badges(self, channel_id: str) -> None:
        url = f"https://badges.twitch.tv/v1/badges/channels/{channel_id}/display"
        if url in self._loaded_badge_urls or url in self._badge_fetch_tasks:
            return
        loop = asyncio.get_running_loop()

        async def loader() -> None:
            await self._load_badge_manifest(url)

        task = loop.create_task(loader())
        self._badge_fetch_tasks[url] = task

        def _cleanup(_: asyncio.Task) -> None:
            self._badge_fetch_tasks.pop(url, None)

        task.add_done_callback(_cleanup)

    def _lookup_badge_metadata(self, badge_id: str, version: str) -> dict[str, str]:
        cache = self._badge_cache.get(badge_id, {})
        badge = cache.get(version)
        if badge:
            return badge

        fallback = {
            "broadcaster": "Broadcaster",
            "moderator": "Mod",
            "vip": "VIP",
            "subscriber": "Sub",
            "founder": "Founder",
            "staff": "Staff",
            "partner": "Partner",
            "premium": "Turbo",
        }
        label = fallback.get(badge_id, badge_id.replace("_", " ").title())
        return {"label": label}
