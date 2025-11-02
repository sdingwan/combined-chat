"""Database models for sessions, Kick users, and Twitch users."""
from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class OAuthPlatform(str, enum.Enum):
    """Supported OAuth providers."""

    TWITCH = "twitch"
    KICK = "kick"
    YOUTUBE = "youtube"


class TwitchUser(Base):
    """Stored Twitch identity and tokens."""

    __tablename__ = "twitch_users"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    profile_image_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scope: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    sessions: Mapped[list["Session"]] = relationship(back_populates="twitch_user")


class KickUser(Base):
    """Stored Kick identity and tokens."""

    __tablename__ = "kick_users"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    profile_image_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scope: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    sessions: Mapped[list["Session"]] = relationship(back_populates="kick_user")


class Session(Base):
    """Browser session linking optional Twitch and Kick identities."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    twitch_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("twitch_users.id", ondelete="SET NULL"), nullable=True
    )
    kick_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("kick_users.id", ondelete="SET NULL"), nullable=True
    )
    youtube_user_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("youtube_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    twitch_user: Mapped[Optional[TwitchUser]] = relationship(back_populates="sessions")
    kick_user: Mapped[Optional[KickUser]] = relationship(back_populates="sessions")
    youtube_user: Mapped[Optional["YouTubeUser"]] = relationship(back_populates="sessions")
    oauth_states: Mapped[list["OAuthState"]] = relationship(back_populates="session")


class YouTubeUser(Base):
    """Stored YouTube identity and tokens."""

    __tablename__ = "youtube_users"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    channel_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    profile_image_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    access_token: Mapped[str] = mapped_column(Text, nullable=False)
    refresh_token: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scope: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    sessions: Mapped[list["Session"]] = relationship(back_populates="youtube_user")


class OAuthState(Base):
    """Persisted OAuth state tokens to support multi-worker deployments."""

    __tablename__ = "oauth_states"

    token: Mapped[str] = mapped_column(String(128), primary_key=True)
    platform: Mapped[OAuthPlatform] = mapped_column(Enum(OAuthPlatform), nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    redirect_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    code_verifier: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[Optional[Session]] = relationship(back_populates="oauth_states")


class YouTubeChannelCache(Base):
    """Persisted cache of retrieved YouTube channel metadata."""

    __tablename__ = "youtube_channel_cache"

    handle: Mapped[str] = mapped_column(String(160), primary_key=True)
    channel_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class YouTubeLiveChatCache(Base):
    """Persisted cache of active YouTube live chat identifiers."""

    __tablename__ = "youtube_live_chat_cache"

    handle: Mapped[str] = mapped_column(String(160), primary_key=True)
    channel_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    live_chat_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    video_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
