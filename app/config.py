"""Application configuration utilities."""
from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment driven application settings."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
    )

    database_url: str = Field(
        default="sqlite+aiosqlite:///data/app.db",
        description="SQLAlchemy database URL (async).",
        env="DATABASE_URL",
    )
    twitch_client_id: Optional[str] = Field(default=None, env="TWITCH_CLIENT_ID")
    twitch_client_secret: Optional[str] = Field(default=None, env="TWITCH_CLIENT_SECRET")
    twitch_redirect_uri: Optional[str] = Field(default=None, env="TWITCH_REDIRECT_URI")
    kick_client_id: Optional[str] = Field(default=None, env="KICK_CLIENT_ID")
    kick_client_secret: Optional[str] = Field(default=None, env="KICK_CLIENT_SECRET")
    kick_redirect_uri: Optional[str] = Field(default=None, env="KICK_REDIRECT_URI")
    kick_scopes: str = Field(default="user:read channel:read chat:read chat:write", env="KICK_SCOPES")
    youtube_client_id: Optional[str] = Field(default=None, env="YOUTUBE_CLIENT_ID")
    youtube_client_secret: Optional[str] = Field(default=None, env="YOUTUBE_CLIENT_SECRET")
    youtube_redirect_uri: Optional[str] = Field(default=None, env="YOUTUBE_REDIRECT_URI")
    youtube_api_key: Optional[str] = Field(default=None, env="YOUTUBE_API_KEY")
    session_cookie_name: str = Field(default="combined_chat_session")
    session_secret: str = Field(
        default_factory=lambda: secrets.token_urlsafe(32),
        env="SESSION_SECRET",
    )
    session_ttl_seconds: int = Field(default=60 * 60 * 24 * 7, env="SESSION_TTL_SECONDS")
    oauth_state_ttl_seconds: int = Field(default=600, env="OAUTH_STATE_TTL_SECONDS")
    frontend_base_url: str = Field(default="http://localhost:8000")

@lru_cache
def get_settings() -> Settings:
    """Return cached application settings."""

    return Settings()


settings = get_settings()
