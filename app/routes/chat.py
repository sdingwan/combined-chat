"""Routes for sending chat messages using linked OAuth accounts."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import get_current_user
from app.config import settings
from app.db import get_session
from app.models import KickUser, OAuthPlatform, TwitchUser

router = APIRouter(prefix="/chat", tags=["chat"])

TWITCH_CHAT_ENDPOINT = "https://api.twitch.tv/helix/chat/messages"
TWITCH_USERS_ENDPOINT = "https://api.twitch.tv/helix/users"
TWITCH_BAN_ENDPOINT = "https://api.twitch.tv/helix/moderation/bans"

KICK_CHAT_ENDPOINT = "https://api.kick.com/public/v1/chat"
KICK_CHANNELS_ENDPOINT = "https://api.kick.com/public/v1/channels"
KICK_BAN_ENDPOINT = "https://api.kick.com/public/v1/moderation/bans"


class SendChatRequest(BaseModel):
    platform: OAuthPlatform
    channel: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=500)


class SendChatResponse(BaseModel):
    platform: OAuthPlatform
    channel: str
    message: str
    status: str


class ModerationAction(str, Enum):
    BAN = "ban"
    TIMEOUT = "timeout"
    UNBAN = "unban"
    UNTIMEOUT = "untimeout"


class ModerateChatRequest(BaseModel):
    platform: OAuthPlatform
    channel: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=64)
    action: ModerationAction
    duration: Optional[int] = Field(default=None, ge=1, le=14 * 24 * 60 * 60)
    target_id: Optional[str] = Field(default=None, min_length=1, max_length=128)


class ModerateChatResponse(BaseModel):
    platform: OAuthPlatform
    channel: str
    target: str
    action: ModerationAction
    status: str

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _safe_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text


def _normalise_kick_slug(value: str) -> str:
    slug = value.strip().lower()
    slug = slug.replace("https://kick.com/", "").replace("http://kick.com/", "")
    slug = slug.replace("/", "")
    slug = slug.replace(" ", "")
    return slug.replace("_", "-")


def _normalise_target(value: str) -> str:
    handle = value.strip()
    if handle.startswith("@"):
        handle = handle[1:]
    if not handle:
        return ""
    # Only keep the first token; moderation targets cannot contain spaces
    handle = handle.split()[0]
    return handle


def _account_has_scope(scope: Optional[str], required: str) -> bool:
    if not scope:
        return False
    return required in scope.split()


@router.post("/send", response_model=SendChatResponse)
async def send_chat_message(
    payload: SendChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> SendChatResponse:
    context = await get_current_user(db, request)
    if not context:
        raise HTTPException(status_code=401, detail="Authentication required")

    if payload.platform is OAuthPlatform.TWITCH:
        account = context.twitch_user
        if not account:
            raise HTTPException(status_code=400, detail="No linked account for platform")
        await _send_twitch_message(db, account, payload.channel, payload.message)
    elif payload.platform is OAuthPlatform.KICK:
        account = context.kick_user
        if not account:
            raise HTTPException(status_code=400, detail="No linked account for platform")
        await _send_kick_message(db, account, payload.channel, payload.message)
    else:
        raise HTTPException(status_code=400, detail="Unsupported platform")

    return SendChatResponse(
        platform=payload.platform,
        channel=payload.channel,
        message=payload.message,
        status="sent",
    )


@router.post("/moderate", response_model=ModerateChatResponse)
async def moderate_chat_action(
    payload: ModerateChatRequest,
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> ModerateChatResponse:
    context = await get_current_user(db, request)
    if not context:
        raise HTTPException(status_code=401, detail="Authentication required")

    channel = payload.channel.strip()
    if not channel:
        raise HTTPException(status_code=400, detail="Channel is required")

    target = _normalise_target(payload.target)
    if not target:
        raise HTTPException(status_code=400, detail="Invalid target user")

    duration = payload.duration
    if payload.action is ModerationAction.TIMEOUT and duration is None:
        raise HTTPException(status_code=400, detail="Timeout requires a duration")

    if payload.platform is OAuthPlatform.TWITCH:
        account = context.twitch_user
        if not account:
            raise HTTPException(status_code=400, detail="No linked account for platform")
        await _moderate_twitch(
            db,
            account,
            channel,
            target,
            payload.action,
            duration,
            payload.target_id,
        )
    elif payload.platform is OAuthPlatform.KICK:
        account = context.kick_user
        if not account:
            raise HTTPException(status_code=400, detail="No linked account for platform")
        await _moderate_kick(
            db,
            account,
            channel,
            target,
            payload.action,
            duration,
            payload.target_id,
        )
    else:
        raise HTTPException(status_code=400, detail="Unsupported platform")

    return ModerateChatResponse(
        platform=payload.platform,
        channel=channel,
        target=target,
        action=payload.action,
        status="ok",
    )


async def _kick_request(
    db: AsyncSession,
    account: KickUser,
    method: str,
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    json: Any = None,
) -> httpx.Response:
    headers = {
        "Authorization": f"Bearer {account.access_token}",
        "Accept": "application/json",
    }
    if json is not None:
        headers["Content-Type"] = "application/json"

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json,
        )

    if response.status_code == 401 and account.refresh_token:
        await _refresh_kick_token(db, account)
        headers["Authorization"] = f"Bearer {account.access_token}"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json,
            )

    return response


async def _resolve_kick_broadcaster(
    db: AsyncSession, account: KickUser, slug: str
) -> int:
    response = await _kick_request(
        db,
        account,
        "GET",
        KICK_CHANNELS_ENDPOINT,
        params={"slug": slug},
    )

    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="Kick authentication expired; please re-authenticate")
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Kick channel not found")
    if response.status_code != 200:
        detail = {
            "status": response.status_code,
            "payload": _safe_body(response) if response.content else None,
        }
        raise HTTPException(status_code=502, detail={"kick_error": detail})

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Invalid Kick channel response") from exc

    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        channel_meta = data[0] if data else None
    elif isinstance(data, dict):
        channel_meta = data
    else:
        channel_meta = None

    if not channel_meta:
        raise HTTPException(status_code=404, detail="Kick channel not found")

    user_dict = channel_meta.get("user") if isinstance(channel_meta.get("user"), dict) else None
    broadcaster = (
        channel_meta.get("broadcaster_user_id")
        or channel_meta.get("user_id")
        or channel_meta.get("id")
        or (user_dict.get("id") if user_dict else None)
    )
    if broadcaster is None:
        raise HTTPException(status_code=502, detail="Kick channel missing broadcaster id")
    try:
        return int(broadcaster)
    except (TypeError, ValueError):
        raise HTTPException(status_code=502, detail="Kick broadcaster id was not numeric")


async def _send_twitch_message(
    db: AsyncSession,
    account: TwitchUser,
    channel: str,
    message: str,
) -> None:
    if not settings.twitch_client_id or not settings.twitch_client_secret:
        raise HTTPException(status_code=503, detail="Twitch OAuth not configured")

    access_token = await _ensure_twitch_token(db, account)
    broadcaster_id = await _lookup_twitch_broadcaster(access_token, channel)
    sender_id = account.id

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Client-Id": settings.twitch_client_id,
        "Content-Type": "application/json",
    }
    payload = {
        "broadcaster_id": broadcaster_id,
        "sender_id": sender_id,
        "message": message,
    }

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(TWITCH_CHAT_ENDPOINT, json=payload, headers=headers)

    if response.status_code == 401 and account.refresh_token:
        access_token = await _refresh_twitch_token(db, account)
        headers["Authorization"] = f"Bearer {access_token}"
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(TWITCH_CHAT_ENDPOINT, json=payload, headers=headers)

    if response.status_code != 200:
        detail = {
            "status": response.status_code,
            "payload": _safe_body(response) if response.content else None,
        }
        raise HTTPException(status_code=502, detail={"twitch_error": detail})


async def _moderate_twitch(
    db: AsyncSession,
    account: TwitchUser,
    channel: str,
    target: str,
    action: ModerationAction,
    duration: Optional[int],
    target_id: Optional[str],
) -> None:
    if not settings.twitch_client_id or not settings.twitch_client_secret:
        raise HTTPException(status_code=503, detail="Twitch OAuth not configured")
    if not _account_has_scope(account.scope, "moderator:manage:banned_users"):
        raise HTTPException(
            status_code=403,
            detail="Twitch account missing moderator permissions; relink with moderator access.",
        )

    access_token = await _ensure_twitch_token(db, account)
    broadcaster_id = await _lookup_twitch_broadcaster(access_token, channel)
    resolved_target_id = (target_id or "").strip()
    if not resolved_target_id:
        resolved_target_id = await _lookup_twitch_user(access_token, target)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Client-Id": settings.twitch_client_id,
        "Content-Type": "application/json",
    }
    params = {
        "broadcaster_id": broadcaster_id,
        "moderator_id": account.id,
    }
    if action in {ModerationAction.BAN, ModerationAction.TIMEOUT}:
        body: dict[str, Any] = {"data": {"user_id": resolved_target_id}}
        if action is ModerationAction.TIMEOUT:
            if duration is None:
                raise HTTPException(status_code=400, detail="Timeout requires a duration")
            body["data"]["duration"] = duration

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                TWITCH_BAN_ENDPOINT, params=params, json=body, headers=headers
            )

        if response.status_code == 401 and account.refresh_token:
            access_token = await _refresh_twitch_token(db, account)
            headers["Authorization"] = f"Bearer {access_token}"
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    TWITCH_BAN_ENDPOINT, params=params, json=body, headers=headers
                )

        if response.status_code in {200, 201}:
            return
    elif action in {ModerationAction.UNBAN, ModerationAction.UNTIMEOUT}:
        delete_params = dict(params)
        delete_params["user_id"] = resolved_target_id

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.delete(
                TWITCH_BAN_ENDPOINT, params=delete_params, headers=headers
            )

        if response.status_code == 401 and account.refresh_token:
            access_token = await _refresh_twitch_token(db, account)
            headers["Authorization"] = f"Bearer {access_token}"
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.delete(
                    TWITCH_BAN_ENDPOINT, params=delete_params, headers=headers
                )

        if response.status_code in {200, 204}:
            return
    else:  # pragma: no cover - defensive
        raise HTTPException(status_code=400, detail="Unsupported moderation action")

    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Twitch target user not found")
    if response.status_code == 403:
        raise HTTPException(
            status_code=403,
            detail="Twitch rejected the moderation request; ensure you are a moderator for this channel.",
        )
    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="Twitch authentication expired; please re-authenticate")

    detail = {
        "status": response.status_code,
        "payload": _safe_body(response) if response.content else None,
    }
    raise HTTPException(status_code=502, detail={"twitch_error": detail})


async def _lookup_twitch_broadcaster(access_token: str, channel: str) -> str:
    params = {"login": channel.lower()}
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Client-Id": settings.twitch_client_id,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(TWITCH_USERS_ENDPOINT, params=params, headers=headers)
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Unable to resolve Twitch broadcaster")

    data = response.json().get("data") or []
    if not data:
        raise HTTPException(status_code=404, detail="Twitch broadcaster not found")
    return data[0].get("id")


async def _lookup_twitch_user(access_token: str, username: str) -> str:
    params = {"login": username.lower()}
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Client-Id": settings.twitch_client_id,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(TWITCH_USERS_ENDPOINT, params=params, headers=headers)
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Unable to resolve Twitch user")

    data = response.json().get("data") or []
    if not data:
        raise HTTPException(status_code=404, detail="Twitch target user not found")
    user_id = data[0].get("id")
    if not user_id:
        raise HTTPException(status_code=502, detail="Twitch response missing user id")
    return user_id


async def _ensure_twitch_token(db: AsyncSession, account: TwitchUser) -> str:
    if not account.token_expires_at:
        return account.access_token
    expires_at = _aware(account.token_expires_at)
    if expires_at > _now() + timedelta(seconds=60):
        return account.access_token
    if not account.refresh_token:
        raise HTTPException(status_code=401, detail="Twitch token expired; re-authenticate")
    return await _refresh_twitch_token(db, account)


async def _refresh_twitch_token(db: AsyncSession, account: TwitchUser) -> str:
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            "https://id.twitch.tv/oauth2/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": account.refresh_token,
                "client_id": settings.twitch_client_id,
                "client_secret": settings.twitch_client_secret,
            },
        )
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Unable to refresh Twitch token")

    payload = response.json()
    account.access_token = payload["access_token"]
    account.refresh_token = payload.get("refresh_token", account.refresh_token)
    scope = payload.get("scope", [])
    if isinstance(scope, list):
        account.scope = " ".join(scope)
    elif isinstance(scope, str):
        account.scope = " ".join(scope.split())
    expires_in = payload.get("expires_in")
    account.token_expires_at = (
        _now() + timedelta(seconds=expires_in)
        if expires_in is not None
        else None
    )
    await db.commit()
    await db.refresh(account)
    return account.access_token


async def _send_kick_message(
    db: AsyncSession,
    account: KickUser,
    channel: str,
    message: str,
) -> None:
    slug = _normalise_kick_slug(channel)
    broadcaster_id = await _resolve_kick_broadcaster(db, account, slug)

    chat_body = {
        "type": "user",
        "content": message,
        "broadcaster_user_id": broadcaster_id,
    }
    response = await _kick_request(
        db,
        account,
        "POST",
        KICK_CHAT_ENDPOINT,
        json=chat_body,
    )

    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="Kick authentication expired; please re-authenticate")
    if response.status_code not in {200, 201}:
        detail = {
            "status": response.status_code,
            "payload": _safe_body(response) if response.content else None,
        }
        raise HTTPException(status_code=502, detail={"kick_error": detail})


async def _moderate_kick(
    db: AsyncSession,
    account: KickUser,
    channel: str,
    target: str,
    action: ModerationAction,
    duration: Optional[int],
    target_id: Optional[str],
) -> None:
    slug = _normalise_kick_slug(channel)
    broadcaster_id = await _resolve_kick_broadcaster(db, account, slug)

    if not account.access_token:
        raise HTTPException(status_code=401, detail="Kick authentication required")

    if not action:
        raise HTTPException(status_code=400, detail="Missing moderation action")

    resolved_user_id: Optional[int] = None
    if target_id:
        try:
            resolved_user_id = int(target_id)
        except (TypeError, ValueError):
            resolved_user_id = None
    elif target:
        try:
            resolved_user_id = int(target)
        except ValueError:
            resolved_user_id = None

    # Kick chat payloads include user ids; require callers to provide one via target_id
    # because the public API does not support username lookups yet.
    if resolved_user_id is None:
        raise HTTPException(
            status_code=400,
            detail="Kick moderation requires a numeric user id; try selecting the user from chat.",
        )

    if action in {ModerationAction.BAN, ModerationAction.TIMEOUT}:
        request_body: dict[str, Any] = {
            "broadcaster_user_id": broadcaster_id,
            "user_id": resolved_user_id,
        }

        if action is ModerationAction.TIMEOUT:
            if duration is None:
                raise HTTPException(status_code=400, detail="Timeout requires a duration")
            # Kick API expects minutes (1-10080). Clamp to allowed range.
            minutes = max(1, min(10080, math.ceil(duration / 60)))
            request_body["duration"] = minutes
            request_body["reason"] = "Timeout via CombinedChat"
        else:
            request_body["reason"] = "Ban via CombinedChat"

        response = await _kick_request(
            db,
            account,
            "POST",
            KICK_BAN_ENDPOINT,
            json=request_body,
        )
    elif action in {ModerationAction.UNBAN, ModerationAction.UNTIMEOUT}:
        response = await _kick_request(
            db,
            account,
            "DELETE",
            KICK_BAN_ENDPOINT,
            json={
                "broadcaster_user_id": broadcaster_id,
                "user_id": resolved_user_id,
            },
        )
    else:  # pragma: no cover - defensive
        raise HTTPException(status_code=400, detail="Unsupported moderation action")

    if response.status_code == 401:
        raise HTTPException(status_code=401, detail="Kick authentication expired; please re-authenticate")
    if response.status_code == 403:
        raise HTTPException(status_code=403, detail="Kick rejected the moderation request; ensure you have moderator access.")
    if response.status_code not in {200, 201, 204}:
        detail = {
            "status": response.status_code,
            "payload": _safe_body(response) if response.content else None,
        }
        raise HTTPException(status_code=502, detail={"kick_error": detail})


async def _refresh_kick_token(db: AsyncSession, account: KickUser) -> None:
    if not account.refresh_token:
        raise HTTPException(status_code=401, detail="Kick token expired; re-authenticate")
    if not settings.kick_client_id or not settings.kick_client_secret:
        raise HTTPException(status_code=503, detail="Kick OAuth not configured")

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            "https://id.kick.com/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": account.refresh_token,
                "client_id": settings.kick_client_id,
                "client_secret": settings.kick_client_secret,
            },
        )
    if response.status_code != 200:
        raise HTTPException(status_code=401, detail="Unable to refresh Kick token")

    payload = response.json()
    account.access_token = payload["access_token"]
    account.refresh_token = payload.get("refresh_token", account.refresh_token)
    scope = payload.get("scope")
    if isinstance(scope, list):
        account.scope = " ".join(scope)
    elif isinstance(scope, str):
        account.scope = " ".join(scope.split())
    expires_in = payload.get("expires_in")
    account.token_expires_at = (
        _now() + timedelta(seconds=expires_in)
        if expires_in is not None
        else None
    )
    await db.commit()
    await db.refresh(account)
