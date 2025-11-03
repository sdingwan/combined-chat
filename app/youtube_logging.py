"""Helper utilities for logging YouTube Data API quota usage."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional


_logger = logging.getLogger("youtube.quota")


def log_quota_call(endpoint: str, *, reason: str, params: Optional[Dict[str, Any]] = None) -> None:
    """Log a YouTube API request that consumes quota.

    Parameters
    ----------
    endpoint:
        The YouTube Data API endpoint being called (e.g. ``liveChatMessages.list``).
    reason:
        Short description of why the call is being made (polling, send message, etc.).
    params:
        Optional dictionary of safe, non-sensitive metadata to include in the logs.
    """

    safe_params: Dict[str, Any]
    if params:
        safe_params = {}
        for key, value in params.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                safe_params[key] = value
            else:
                safe_params[key] = str(value)
    else:
        safe_params = {}

    _logger.info("YouTube quota call: %s (%s) %s", endpoint, reason, safe_params)

