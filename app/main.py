import asyncio
import os
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

from app.auth.routes import router as auth_router
from app.chat_sources.kick import KickChatClient
from app.chat_sources.twitch import TwitchChatClient
from app.chat_sources.youtube import YouTubeChatClient
from app.db import init_db
from app.routes.chat import router as chat_router

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("combined_chat")

app = FastAPI(title="Combined Twitch & Kick Chat")

static_dir = Path(__file__).resolve().parent.parent / "static"


class BoundedCacheStaticFiles(StaticFiles):
    """Static file handler that keeps cache lifetime short for easier deploys."""

    def __init__(self, *args, max_age: int = 60, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._max_age = max_age

    def set_response_headers(
        self, response: Response, file: Path, stat_result: "os.stat_result", scope: dict
    ) -> None:
        super().set_response_headers(response, file, stat_result, scope)
        response.headers["Cache-Control"] = f"public, max-age={self._max_age}"
        response.headers["Expires"] = "0"


app.mount(
    "/static",
    BoundedCacheStaticFiles(directory=static_dir, max_age=12 * 60 * 60),
    name="static",
)
app.include_router(auth_router)
app.include_router(chat_router)


@app.on_event("startup")
async def startup() -> None:
    await init_db()


@app.get("/")
async def index() -> HTMLResponse:
    html_path = static_dir / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return HTMLResponse(
        html_path.read_text(),
        headers={"Cache-Control": "no-store"},
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    stop_event = asyncio.Event()
    queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()
    listener_tasks: list[asyncio.Task] = []
    forward_task: Optional[asyncio.Task] = None
    completion_task: Optional[asyncio.Task] = None

    try:
        try:
            init = await websocket.receive_json()
        except Exception as exc:
            await websocket.close(code=4000)
            logger.debug("Websocket closed before init payload: %s", exc)
            return

        if init.get("action") != "subscribe":
            await websocket.send_json({"type": "error", "message": "Expected subscribe action"})
            await websocket.close(code=4001)
            return

        def _normalise_channels(value: Any) -> list[str]:
            if value is None:
                return []
            raw_items: list[str]
            if isinstance(value, str):
                # Allow comma or newline separated strings for backwards compatibility
                raw_items = [item.strip() for item in value.replace("\n", ",").split(",")]
            elif isinstance(value, (list, tuple, set)):
                raw_items = []
                for item in value:
                    if isinstance(item, str):
                        raw_items.append(item.strip())
                    elif item is not None:
                        raw_items.append(str(item).strip())
            else:
                raw_items = []

            seen: set[str] = set()
            cleaned: list[str] = []
            for item in raw_items:
                if not item:
                    continue
                normalised = item.lstrip("#").strip()
                if not normalised:
                    continue
                key = normalised.lower()
                if key in seen:
                    continue
                seen.add(key)
                cleaned.append(normalised)
                if len(cleaned) >= 10:
                    break
            return cleaned

        twitch_channels = _normalise_channels(init.get("twitch"))
        kick_channels = _normalise_channels(init.get("kick"))
        youtube_channels = _normalise_channels(init.get("youtube"))

        if not twitch_channels and not kick_channels and not youtube_channels:
            await websocket.send_json(
                {"type": "error", "message": "Please provide at least one streamer name"}
            )
            await websocket.close(code=4002)
            return

        twitch_clients: list[TwitchChatClient] = []
        kick_clients: list[KickChatClient] = []
        youtube_clients: list[YouTubeChatClient] = []

        twitch_errors: list[tuple[str, str]] = []
        kick_errors: list[tuple[str, str]] = []
        youtube_errors: list[tuple[str, str]] = []

        for channel in twitch_channels:
            client = TwitchChatClient(channel, queue, stop_event)
            try:
                await client.ensure_channel_exists()
            except RuntimeError as exc:
                twitch_errors.append((channel, str(exc)))
            else:
                twitch_clients.append(client)

        for channel in kick_channels:
            client = KickChatClient(channel, queue, stop_event)
            try:
                await client.ensure_channel_exists()
            except RuntimeError as exc:
                kick_errors.append((channel, str(exc)))
            else:
                kick_clients.append(client)

        for channel in youtube_channels:
            client = YouTubeChatClient(channel, queue, stop_event)
            try:
                await client.ensure_channel_exists()
            except RuntimeError as exc:
                youtube_errors.append((channel, str(exc)))
            else:
                youtube_clients.append(client)

        for channel, message in twitch_errors:
            await websocket.send_json(
                {"platform": "twitch", "channel": channel, "type": "error", "message": message}
            )
        for channel, message in kick_errors:
            await websocket.send_json(
                {"platform": "kick", "channel": channel, "type": "error", "message": message}
            )
        for channel, message in youtube_errors:
            await websocket.send_json(
                {"platform": "youtube", "channel": channel, "type": "error", "message": message}
            )

        if not twitch_clients and not kick_clients and not youtube_clients:
            await websocket.close(code=4405)
            return

        for client in twitch_clients:
            listener_tasks.append(asyncio.create_task(client.run()))
        for client in kick_clients:
            listener_tasks.append(asyncio.create_task(client.run()))
        for client in youtube_clients:
            listener_tasks.append(asyncio.create_task(client.run()))

        forward_task = asyncio.create_task(_forward_messages(websocket, queue, stop_event))
        completion_task = asyncio.create_task(_complete_on_listeners(listener_tasks, stop_event))

        await forward_task
    except WebSocketDisconnect:
        logger.info("Client disconnected")
    finally:
        stop_event.set()
        for task in listener_tasks:
            if not task.done():
                task.cancel()

        all_tasks: list[asyncio.Task] = list(listener_tasks)

        if forward_task is not None:
            if not forward_task.done():
                forward_task.cancel()
            all_tasks.append(forward_task)

        if completion_task is not None:
            if not completion_task.done():
                completion_task.cancel()
            all_tasks.append(completion_task)

        if all_tasks:
            await asyncio.gather(*all_tasks, return_exceptions=True)


async def _forward_messages(
    websocket: WebSocket, queue: "asyncio.Queue[dict[str, Any]]", stop_event: asyncio.Event
) -> None:
    while True:
        if stop_event.is_set() and queue.empty():
            break
        try:
            payload = await asyncio.wait_for(queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue
        try:
            await websocket.send_json(payload)
        except WebSocketDisconnect:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Failed to send payload: %s", exc)
            break


async def _complete_on_listeners(tasks: list[asyncio.Task], stop_event: asyncio.Event) -> None:
    if not tasks:
        stop_event.set()
        return
    await asyncio.gather(*tasks, return_exceptions=True)
    stop_event.set()
