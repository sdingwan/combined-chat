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

    async def run(self) -> None:
        nickname = f"justinfan{secrets.randbelow(1_000_000):06d}"
        try:
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

        try:
            prefix, _, remainder = payload.partition(" PRIVMSG ")
            username = prefix.split("!")[0][1:]
            display_name = tags.get("display-name") or username
            _, _, text = remainder.partition(" :")
            message_payload = {
                "platform": "twitch",
                "type": "chat",
                "user": display_name,
                "message": text,
            }

            color = tags.get("color")
            if color:
                message_payload["color"] = color

            emotes = self._parse_emotes(tags.get("emotes"), text)
            if emotes:
                message_payload["emotes"] = emotes

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
