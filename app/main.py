import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.auth.routes import router as auth_router
from app.chat_sources.kick import KickChatClient
from app.chat_sources.twitch import TwitchChatClient
from app.db import init_db
from app.routes.chat import router as chat_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("combined_chat")

app = FastAPI(title="Combined Twitch & Kick Chat")

static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")
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
    return HTMLResponse(html_path.read_text())


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    stop_event = asyncio.Event()
    queue: "asyncio.Queue[dict[str, Any]]" = asyncio.Queue()
    listener_tasks: list[asyncio.Task] = []

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

    twitch_channel = str(init.get("twitch", "")).strip()
    kick_channel = str(init.get("kick", "")).strip()

    if not twitch_channel and not kick_channel:
        await websocket.send_json(
            {"type": "error", "message": "Please provide at least one streamer name"}
        )
        await websocket.close(code=4002)
        return

    if twitch_channel:
        listener_tasks.append(
            asyncio.create_task(TwitchChatClient(twitch_channel, queue, stop_event).run())
        )
    if kick_channel:
        listener_tasks.append(
            asyncio.create_task(KickChatClient(kick_channel, queue, stop_event).run())
        )

    forward_task = asyncio.create_task(_forward_messages(websocket, queue, stop_event))
    completion_task = asyncio.create_task(_complete_on_listeners(listener_tasks, stop_event))

    try:
        await forward_task
    except WebSocketDisconnect:
        logger.info("Client disconnected")
    finally:
        stop_event.set()
        for task in listener_tasks:
            task.cancel()
        forward_task.cancel()
        completion_task.cancel()
        await asyncio.gather(*listener_tasks, return_exceptions=True)
        await asyncio.gather(forward_task, return_exceptions=True)
        await asyncio.gather(completion_task, return_exceptions=True)


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
