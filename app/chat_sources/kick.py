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
                    "message": f"Disconnected from Kick chat for {self.channel}",
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
                            "message": f"Connected to Kick chat for {self.channel}",
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
        }

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
