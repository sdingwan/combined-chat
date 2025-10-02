import asyncio
import json
import logging
from typing import Optional

import httpx
import websockets
from websockets.exceptions import ConnectionClosed

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

    async def run(self) -> None:
        try:
            chatroom_id = await self._fetch_chatroom_id()
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Failed to resolve Kick channel %s: %s", self.channel, exc)
            await self.queue.put(
                {
                    "platform": "kick",
                    "type": "error",
                    "message": f"Kick channel lookup failed: {exc}",
                }
            )
            await self.queue.put(
                {
                    "platform": "kick",
                    "type": "status",
                    "message": f"Stopped listening to {self.channel}",
                }
            )
            return

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
                    "type": "error",
                    "message": f"Kick connection failed: {exc}",
                }
            )
        finally:
            await self.queue.put(
                {
                    "platform": "kick",
                    "type": "status",
                    "message": f"Stopped listening to {self.channel}",
                }
            )

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
                            "type": "status",
                            "message": f"Listening to Kick chat for {self.channel}",
                        }
                    )
            elif event == "pusher:ping":
                await socket.send(self.PONG_PAYLOAD)
            elif event == "App\\Events\\ChatMessageEvent":
                payload = self._parse_chat_message(message)
                if payload:
                    await self.queue.put(payload)
            elif event == "pusher:error":
                error_data = message.get("data") or {}
                await self.queue.put(
                    {
                        "platform": "kick",
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

        return int(chatroom_id)

    def _decode_pusher_payload(self, raw: str) -> Optional[dict]:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Invalid JSON from Kick websocket: %s", raw)
            return None

    def _parse_chat_message(self, message: dict) -> Optional[dict]:
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
        if isinstance(sender, dict):
            username = sender.get("username") or sender.get("slug")
        else:
            username = sender

        if not content or not username:
            return None

        return {
            "platform": "kick",
            "type": "chat",
            "user": username,
            "message": content,
        }
