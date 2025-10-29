"""Authentication and OAuth routes."""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.session import destroy_session, ensure_session, get_current_user
from app.auth.state import consume_state, create_state
from app.config import settings
from app.db import get_session
from app.models import KickUser, OAuthPlatform, TwitchUser, YouTubeUser

TWITCH_AUTHORIZE_URL = "https://id.twitch.tv/oauth2/authorize"
TWITCH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
TWITCH_USER_URL = "https://api.twitch.tv/helix/users"
TWITCH_SCOPES = [
    "chat:read",
    "chat:edit",
    "user:write:chat",
    "moderator:manage:banned_users",
]

KICK_AUTHORIZE_URL = "https://id.kick.com/oauth/authorize"
KICK_TOKEN_URL = "https://id.kick.com/oauth/token"
KICK_USER_URL = "https://api.kick.com/public/v1/users"

YOUTUBE_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
YOUTUBE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
YOUTUBE_CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"


logger = logging.getLogger(__name__)


def _scope_list(raw_scopes: Optional[str]) -> list[str]:
    if not raw_scopes:
        return []
    return [scope for scope in raw_scopes.replace(",", " ").split() if scope]


KICK_SCOPES = _scope_list(settings.kick_scopes)
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/userinfo.profile",
]

router = APIRouter(prefix="/auth", tags=["auth"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _scope_string(scopes: list[str]) -> str:
    return " ".join(sorted(set(scopes)))


def _safe_redirect_url(path: Optional[str]) -> str:
    base = settings.frontend_base_url.rstrip("/")
    if not path:
        return base
    parsed = urllib.parse.urlparse(path)
    if parsed.scheme or parsed.netloc:
        # absolute URLs are not permitted to avoid open redirects
        return base
    if not path.startswith("/"):
        path = "/" + path
    return base + path


def _generate_pkce_pair() -> tuple[str, str]:
    """Return a (verifier, challenge) tuple for PKCE."""

    verifier = base64.urlsafe_b64encode(os.urandom(48)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return verifier, challenge


async def _upsert_twitch_user(
    db: AsyncSession,
    *,
    platform_user_id: str,
    username: str,
    display_name: Optional[str],
    profile_image_url: Optional[str],
    access_token: str,
    refresh_token: Optional[str],
    scopes: list[str],
    expires_in: Optional[int],
) -> TwitchUser:
    record = await db.get(TwitchUser, platform_user_id)
    expires_at = (
        _now() + timedelta(seconds=expires_in)
        if expires_in is not None
        else None
    )

    if record:
        record.username = username
        record.display_name = display_name or record.display_name
        record.profile_image_url = profile_image_url or record.profile_image_url
        record.access_token = access_token
        record.refresh_token = refresh_token or record.refresh_token
        record.scope = _scope_string(scopes)
        record.token_expires_at = expires_at
    else:
        record = TwitchUser(
            id=platform_user_id,
            username=username,
            display_name=display_name,
            profile_image_url=profile_image_url,
            access_token=access_token,
            refresh_token=refresh_token,
            scope=_scope_string(scopes),
            token_expires_at=expires_at,
        )
        db.add(record)

    await db.commit()
    await db.refresh(record)
    return record


async def _upsert_kick_user(
    db: AsyncSession,
    *,
    platform_user_id: str,
    username: str,
    display_name: Optional[str],
    profile_image_url: Optional[str],
    access_token: str,
    refresh_token: Optional[str],
    scopes: list[str],
    expires_in: Optional[int],
) -> KickUser:
    record = await db.get(KickUser, platform_user_id)
    expires_at = (
        _now() + timedelta(seconds=expires_in)
        if expires_in is not None
        else None
    )

    if record:
        record.username = username
        record.display_name = display_name or record.display_name
        record.profile_image_url = profile_image_url or record.profile_image_url
        record.access_token = access_token
        record.refresh_token = refresh_token or record.refresh_token
        record.scope = _scope_string(scopes)
        record.token_expires_at = expires_at
    else:
        record = KickUser(
            id=platform_user_id,
            username=username,
            display_name=display_name,
            profile_image_url=profile_image_url,
            access_token=access_token,
            refresh_token=refresh_token,
            scope=_scope_string(scopes),
            token_expires_at=expires_at,
        )
        db.add(record)

    await db.commit()
    await db.refresh(record)
    return record


async def _upsert_youtube_user(
    db: AsyncSession,
    *,
    platform_user_id: str,
    channel_id: Optional[str],
    display_name: Optional[str],
    profile_image_url: Optional[str],
    access_token: str,
    refresh_token: Optional[str],
    scopes: list[str],
    expires_in: Optional[int],
) -> YouTubeUser:
    record = await db.get(YouTubeUser, platform_user_id)
    expires_at = (
        _now() + timedelta(seconds=expires_in)
        if expires_in is not None
        else None
    )

    if record:
        record.channel_id = channel_id or record.channel_id
        record.display_name = display_name or record.display_name
        record.profile_image_url = profile_image_url or record.profile_image_url
        record.access_token = access_token
        record.refresh_token = refresh_token or record.refresh_token
        record.scope = _scope_string(scopes)
        record.token_expires_at = expires_at
    else:
        record = YouTubeUser(
            id=platform_user_id,
            channel_id=channel_id,
            display_name=display_name,
            profile_image_url=profile_image_url,
            access_token=access_token,
            refresh_token=refresh_token,
            scope=_scope_string(scopes),
            token_expires_at=expires_at,
        )
        db.add(record)

    await db.commit()
    await db.refresh(record)
    return record


@router.get("/status")
async def auth_status(
    request: Request, db: AsyncSession = Depends(get_session)
) -> JSONResponse:
    """Return information about the logged-in user and linked accounts."""

    context = await get_current_user(db, request)
    if not context:
        return JSONResponse({"authenticated": False, "accounts": [], "user": None})

    accounts: list[dict[str, Any]] = []
    if context.twitch_user:
        expires_at = context.twitch_user.token_expires_at
        accounts.append(
            {
                "platform": OAuthPlatform.TWITCH.value,
                "username": context.twitch_user.username,
                "display_name": context.twitch_user.display_name,
                "profile_image_url": context.twitch_user.profile_image_url,
                "scopes": (context.twitch_user.scope or "").split(),
                "expires_at": expires_at.isoformat() if expires_at else None,
            }
        )

    if context.kick_user:
        expires_at = context.kick_user.token_expires_at
        accounts.append(
            {
                "platform": OAuthPlatform.KICK.value,
                "username": context.kick_user.username,
                "display_name": context.kick_user.display_name,
                "profile_image_url": context.kick_user.profile_image_url,
                "scopes": (context.kick_user.scope or "").split(),
                "expires_at": expires_at.isoformat() if expires_at else None,
            }
        )
    if context.youtube_user:
        expires_at = context.youtube_user.token_expires_at
        accounts.append(
            {
                "platform": OAuthPlatform.YOUTUBE.value,
                "username": context.youtube_user.display_name or context.youtube_user.id,
                "display_name": context.youtube_user.display_name,
                "profile_image_url": context.youtube_user.profile_image_url,
                "scopes": (context.youtube_user.scope or "").split(),
                "expires_at": expires_at.isoformat() if expires_at else None,
            }
        )

    authenticated = bool(accounts)
    user_payload: Optional[dict[str, Any]]
    if not authenticated:
        user_payload = None
    else:
        display_name = (
            (context.twitch_user.display_name if context.twitch_user and context.twitch_user.display_name else None)
            or (context.kick_user.display_name if context.kick_user and context.kick_user.display_name else None)
            or (context.youtube_user.display_name if context.youtube_user and context.youtube_user.display_name else None)
            or (context.twitch_user.username if context.twitch_user else None)
            or (context.kick_user.username if context.kick_user else None)
            or (context.youtube_user.id if context.youtube_user else None)
        )
        user_payload = {
            "id": context.session.id,
            "display_name": display_name,
            "twitch_user_id": context.twitch_user.id if context.twitch_user else None,
            "kick_user_id": context.kick_user.id if context.kick_user else None,
            "youtube_user_id": context.youtube_user.id if context.youtube_user else None,
        }

    return JSONResponse(
        {
            "authenticated": authenticated,
            "user": user_payload,
            "accounts": accounts,
        }
    )


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Terminate the current session."""

    await destroy_session(db, response, request)
    return JSONResponse({"success": True})


@router.get("/{platform}/login")
async def oauth_login(
    platform: OAuthPlatform,
    request: Request,
    redirect_path: Optional[str] = None,
    db: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Initiate an OAuth login flow for the specified provider."""

    context = await get_current_user(db, request)
    code_verifier: Optional[str] = None
    code_challenge: Optional[str] = None
    if platform is OAuthPlatform.KICK:
        code_verifier, code_challenge = _generate_pkce_pair()
    state_token = await create_state(
        db=db,
        platform=platform,
        session_id=context.session.id if context else None,
        redirect_path=redirect_path,
        code_verifier=code_verifier,
    )

    if platform is OAuthPlatform.TWITCH:
        if not settings.twitch_client_id or not settings.twitch_redirect_uri:
            raise HTTPException(status_code=503, detail="Twitch OAuth not configured")
        params = {
            "client_id": settings.twitch_client_id,
            "redirect_uri": settings.twitch_redirect_uri,
            "response_type": "code",
            "scope": " ".join(TWITCH_SCOPES),
            "state": state_token,
        }
        url = f"{TWITCH_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
        return RedirectResponse(url)

    if platform is OAuthPlatform.KICK:
        if not settings.kick_client_id or not settings.kick_redirect_uri:
            raise HTTPException(status_code=503, detail="Kick OAuth not configured")
        scopes = KICK_SCOPES or []
        params = {
            "client_id": settings.kick_client_id,
            "redirect_uri": settings.kick_redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state_token,
        }
        if code_challenge:
            params.update({
                "code_challenge_method": "S256",
                "code_challenge": code_challenge,
            })
        url = f"{KICK_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
        return RedirectResponse(url)

    if platform is OAuthPlatform.YOUTUBE:
        if not settings.youtube_client_id or not settings.youtube_redirect_uri:
            raise HTTPException(status_code=503, detail="YouTube OAuth not configured")
        params = {
            "client_id": settings.youtube_client_id,
            "redirect_uri": settings.youtube_redirect_uri,
            "response_type": "code",
            "scope": " ".join(YOUTUBE_SCOPES),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "state": state_token,
            "prompt": "consent",
        }
        url = f"{YOUTUBE_AUTHORIZE_URL}?{urllib.parse.urlencode(params)}"
        return RedirectResponse(url)

    raise HTTPException(status_code=400, detail="Unsupported platform")


@router.get("/twitch/callback")
async def twitch_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if error:
        target = _safe_redirect_url("/?error=" + urllib.parse.quote(error))
        return RedirectResponse(target)
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth parameters")

    state_record = await consume_state(
        db=db,
        platform=OAuthPlatform.TWITCH,
        state_token=state,
    )
    if not state_record:
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    token_payload = await _exchange_twitch_code(code)
    access_token = token_payload["access_token"]
    refresh_token = token_payload.get("refresh_token")
    raw_scopes = token_payload.get("scope")
    if isinstance(raw_scopes, list):
        scopes = raw_scopes
    elif isinstance(raw_scopes, str):
        scopes = raw_scopes.split()
    else:
        scopes = []
    expires_in = token_payload.get("expires_in")

    user_info = await _fetch_twitch_user(access_token)
    data_list = user_info.get("data") or []
    if not data_list:
        raise HTTPException(status_code=500, detail="Unable to fetch Twitch user profile")
    profile = data_list[0]

    platform_user_id = profile.get("id")
    if not platform_user_id:
        raise HTTPException(status_code=500, detail="Twitch response missing user id")

    display_name = profile.get("display_name") or profile.get("login")
    username = profile.get("login") or platform_user_id
    profile_image = profile.get("profile_image_url")

    twitch_user = await _upsert_twitch_user(
        db,
        platform_user_id=platform_user_id,
        username=username,
        display_name=display_name,
        profile_image_url=profile_image,
        access_token=access_token,
        refresh_token=refresh_token,
        scopes=scopes,
        expires_in=expires_in,
    )

    redirect_url = _safe_redirect_url(state_record.redirect_path)
    response = RedirectResponse(redirect_url, status_code=303)
    await ensure_session(db, request, response, twitch_user=twitch_user)
    return response


@router.get("/kick/callback")
async def kick_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if error:
        target = _safe_redirect_url("/?error=" + urllib.parse.quote(error))
        return RedirectResponse(target)
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth parameters")

    state_record = await consume_state(
        db=db,
        platform=OAuthPlatform.KICK,
        state_token=state,
    )
    if not state_record:
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    code_verifier = state_record.code_verifier
    if not code_verifier:
        raise HTTPException(status_code=400, detail="Kick state missing PKCE verifier")

    token_payload = await _exchange_kick_code(code, code_verifier)
    access_token = token_payload["access_token"]
    refresh_token = token_payload.get("refresh_token")
    raw_scope = token_payload.get("scope")
    if isinstance(raw_scope, list):
        scopes = raw_scope
    elif isinstance(raw_scope, str):
        scopes = raw_scope.split()
    else:
        scopes = []
    expires_in = token_payload.get("expires_in")

    profile = await _fetch_kick_user(access_token)
    platform_user_id, username, display_name, profile_image = _extract_kick_identity(profile)

    if not platform_user_id:
        logger.error("Kick profile missing usable identifier: %s", profile)
        raise HTTPException(status_code=500, detail="Kick response missing user details")

    username = username or platform_user_id
    display_name = display_name or username

    kick_user = await _upsert_kick_user(
        db,
        platform_user_id=platform_user_id,
        username=username,
        display_name=display_name,
        profile_image_url=profile_image,
        access_token=access_token,
        refresh_token=refresh_token,
        scopes=scopes,
        expires_in=expires_in,
    )

    redirect_url = _safe_redirect_url(state_record.redirect_path)
    response = RedirectResponse(redirect_url, status_code=303)
    await ensure_session(db, request, response, kick_user=kick_user)
    return response


@router.get("/youtube/callback")
async def youtube_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    if error:
        target = _safe_redirect_url("/?error=" + urllib.parse.quote(error))
        return RedirectResponse(target)
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth parameters")

    state_record = await consume_state(
        db=db,
        platform=OAuthPlatform.YOUTUBE,
        state_token=state,
    )
    if not state_record:
        raise HTTPException(status_code=400, detail="Invalid or expired state")

    token_payload = await _exchange_youtube_code(code)
    access_token = token_payload["access_token"]
    refresh_token = token_payload.get("refresh_token")
    raw_scope = token_payload.get("scope")
    if isinstance(raw_scope, list):
        scopes = raw_scope
    elif isinstance(raw_scope, str):
        scopes = raw_scope.split()
    else:
        scopes = []
    expires_in = token_payload.get("expires_in")

    profile = await _fetch_youtube_profile(access_token)
    platform_user_id = profile.get("id") or profile.get("sub")
    if not platform_user_id:
        raise HTTPException(status_code=500, detail="YouTube profile response missing user id")
    display_name = profile.get("name") or profile.get("given_name")
    profile_image = profile.get("picture")

    channel_id, channel_title, channel_thumbnail = await _fetch_youtube_channel(access_token)
    if channel_title and not display_name:
        display_name = channel_title
    if channel_thumbnail and not profile_image:
        profile_image = channel_thumbnail

    youtube_user = await _upsert_youtube_user(
        db,
        platform_user_id=str(platform_user_id),
        channel_id=channel_id,
        display_name=display_name,
        profile_image_url=profile_image,
        access_token=access_token,
        refresh_token=refresh_token,
        scopes=scopes,
        expires_in=expires_in,
    )

    redirect_url = _safe_redirect_url(state_record.redirect_path)
    response = RedirectResponse(redirect_url, status_code=303)
    await ensure_session(db, request, response, youtube_user=youtube_user)
    return response


async def _exchange_twitch_code(code: str) -> dict[str, Any]:
    if not settings.twitch_client_id or not settings.twitch_client_secret or not settings.twitch_redirect_uri:
        raise HTTPException(status_code=503, detail="Twitch OAuth not configured")

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            TWITCH_TOKEN_URL,
            data={
                "client_id": settings.twitch_client_id,
                "client_secret": settings.twitch_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.twitch_redirect_uri,
            },
        )
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to exchange Twitch token")
    return response.json()


async def _fetch_twitch_user(access_token: str) -> dict[str, Any]:
    if not settings.twitch_client_id:
        raise HTTPException(status_code=503, detail="Twitch OAuth not configured")

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Client-Id": settings.twitch_client_id,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(TWITCH_USER_URL, headers=headers)
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to load Twitch profile")
    return response.json()


async def _exchange_kick_code(code: str, code_verifier: str) -> dict[str, Any]:
    if not settings.kick_client_id or not settings.kick_client_secret or not settings.kick_redirect_uri:
        raise HTTPException(status_code=503, detail="Kick OAuth not configured")

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            KICK_TOKEN_URL,
            data={
                "client_id": settings.kick_client_id,
                "client_secret": settings.kick_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.kick_redirect_uri,
                "code_verifier": code_verifier,
            },
        )
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to exchange Kick token")
    return response.json()


async def _fetch_kick_user(access_token: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(KICK_USER_URL, headers=headers)
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Failed to load Kick profile")
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        profile = data[0] if data else None
    elif isinstance(data, dict):
        profile = data
    else:
        profile = None
    if isinstance(profile, dict):
        nested = profile.get("data")
        if isinstance(nested, dict):
            profile = nested
        elif isinstance(nested, list) and nested:
            profile = nested[0]
    if not isinstance(profile, dict):
        raise HTTPException(status_code=500, detail="Kick profile response missing data")
    return profile


async def _exchange_youtube_code(code: str) -> dict[str, Any]:
    if (
        not settings.youtube_client_id
        or not settings.youtube_client_secret
        or not settings.youtube_redirect_uri
    ):
        raise HTTPException(status_code=503, detail="YouTube OAuth not configured")

    data = {
        "code": code,
        "client_id": settings.youtube_client_id,
        "client_secret": settings.youtube_client_secret,
        "redirect_uri": settings.youtube_redirect_uri,
        "grant_type": "authorization_code",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(YOUTUBE_TOKEN_URL, data=data)

    if response.status_code != 200:
        logger.error("YouTube token exchange failed: %s", response.text)
        raise HTTPException(status_code=502, detail="Failed to exchange YouTube token")
    return response.json()


async def _fetch_youtube_profile(access_token: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {access_token}",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(YOUTUBE_USERINFO_URL, headers=headers)
    if response.status_code != 200:
        logger.error("YouTube userinfo request failed: %s", response.text)
        raise HTTPException(status_code=502, detail="Failed to load YouTube profile")
    payload = response.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=500, detail="YouTube profile response malformed")
    return payload


async def _fetch_youtube_channel(
    access_token: str,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    params = {
        "part": "id,snippet",
        "mine": "true",
        "maxResults": 1,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(YOUTUBE_CHANNELS_URL, params=params, headers=headers)

    if response.status_code != 200:
        logger.info("YouTube channel lookup failed (%s): %s", response.status_code, response.text)
        return None, None, None

    payload = response.json()
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        return None, None, None

    entry = items[0] if isinstance(items[0], dict) else {}
    channel_id = entry.get("id")
    snippet = entry.get("snippet") if isinstance(entry, dict) else None
    title = snippet.get("title") if isinstance(snippet, dict) else None
    thumbnail_url = None
    if isinstance(snippet, dict):
        thumbnails = snippet.get("thumbnails")
        if isinstance(thumbnails, dict):
            for key in ("high", "medium", "default"):
                candidate = thumbnails.get(key)
                if isinstance(candidate, dict):
                    url = candidate.get("url")
                    if isinstance(url, str) and url:
                        thumbnail_url = url
                        break
    return (
        str(channel_id) if channel_id is not None else None,
        title if isinstance(title, str) else None,
        thumbnail_url,
    )


def _extract_kick_identity(
    profile: dict[str, Any]
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Normalize profile payload into identifiers with best-effort fallbacks."""

    def _collect_sources() -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = [profile]
        for key in ("user", "profile", "attributes", "channel", "data"):
            value = profile.get(key)
            if isinstance(value, dict):
                sources.append(value)
            elif isinstance(value, list):
                sources.extend(item for item in value if isinstance(item, dict))
        return sources

    sources = _collect_sources()

    def _first(keys: Iterable[str], allow_zero: bool = False) -> Optional[Any]:
        for source in sources:
            for key in keys:
                value = source.get(key)
                if isinstance(value, (dict, list)):
                    continue
                if value is None:
                    continue
                if value == "" and not allow_zero:
                    continue
                if value == 0 and not allow_zero:
                    continue
                return value
        return None

    raw_user = profile.get("user") if isinstance(profile.get("user"), dict) else None

    user_id = _first(["user_id", "userId", "id"], allow_zero=True)
    if user_id is None and isinstance(raw_user, dict):
        user_id = (
            raw_user.get("id")
            or raw_user.get("user_id")
            or raw_user.get("userId")
        )

    slug_raw = _first(["slug", "username", "name"])
    if isinstance(slug_raw, str):
        slug = slug_raw.strip() or None
    elif slug_raw is not None:
        slug = str(slug_raw)
    else:
        slug = None
    if slug is None and isinstance(raw_user, dict):
        slug_candidate = (
            raw_user.get("slug")
            or raw_user.get("username")
            or raw_user.get("name")
        )
        if isinstance(slug_candidate, str):
            slug = slug_candidate.strip() or None
        elif slug_candidate is not None:
            slug = str(slug_candidate)

    display_name = _first(["display_name", "displayName"]) or (
        raw_user.get("display_name") if isinstance(raw_user, dict) else None
    )

    image_candidate = _first(
        [
            "profile_image",
            "profileImage",
            "profile_image_url",
            "profileImageUrl",
            "profile_picture",
            "profilePicture",
            "avatar",
            "image",
            "picture",
        ]
    )
    if image_candidate is None and isinstance(raw_user, dict):
        image_candidate = (
            raw_user.get("profile_image")
            or raw_user.get("profileImage")
            or raw_user.get("profile_image_url")
            or raw_user.get("profileImageUrl")
            or raw_user.get("profile_picture")
            or raw_user.get("profilePicture")
            or raw_user.get("avatar")
            or raw_user.get("image")
            or raw_user.get("picture")
        )

    platform_user_id: Optional[str]
    if user_id is not None and isinstance(user_id, (str, int)):
        platform_user_id = str(user_id)
    elif isinstance(slug, str) and slug:
        platform_user_id = slug
    else:
        platform_user_id = None

    username_candidate = _first(["username", "slug", "name"])
    if username_candidate is None and isinstance(raw_user, dict):
        username_candidate = (
            raw_user.get("username")
            or raw_user.get("slug")
            or raw_user.get("name")
        )
    if username_candidate is None:
        username_candidate = slug

    if isinstance(username_candidate, str):
        username = username_candidate.strip() or None
    else:
        username = None

    if isinstance(display_name, str):
        display_name = display_name.strip() or None

    if isinstance(image_candidate, str):
        profile_image = image_candidate.strip() or None
    else:
        profile_image = None

    return platform_user_id, username, display_name, profile_image
