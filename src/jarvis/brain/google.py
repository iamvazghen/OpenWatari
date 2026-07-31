"""Shared Google OAuth for Gmail + Calendar — one app, one refresh token.

The owner asked for Gmail "through an app": a real Google Cloud OAuth app. He runs the one-time
consent once (``bench/google_login.py``) which yields a long-lived **refresh token**; Jarvis then
silently exchanges it for short-lived access tokens as needed. The same app/token covers Gmail and
Calendar (request both scopes at consent time).

Deliberately dependency-light: plain REST over ``httpx`` (no ``google-api-python-client``), so the
whole thing is a few HTTP calls. Everything degrades — no credentials means ``configured()`` is
False and the tools say so instead of crashing. Access tokens are cached in-process until ~1 min
before expiry.
"""

from __future__ import annotations

import time

import httpx
from loguru import logger

from jarvis.config import settings

TOKEN_URL = "https://oauth2.googleapis.com/token"

# The scopes the one-time consent must grant. gmail.modify covers read + send + draft; calendar
# covers read + create. Keep in sync with bench/google_login.py.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar",
    # Wearable vitals (Google Fit) — sleep/activity/heart feed the wellbeing + proactive engines.
    # Requested at the same consent so ONE browser click covers mail + calendar + vitals.
    "https://www.googleapis.com/auth/fitness.activity.read",
    "https://www.googleapis.com/auth/fitness.sleep.read",
    "https://www.googleapis.com/auth/fitness.heart_rate.read",
]

_token_cache: dict[str, float | str] = {"value": "", "expires": 0.0}


def configured() -> bool:
    return bool(
        settings.google_client_id
        and settings.google_client_secret
        and settings.google_refresh_token
    )


async def access_token() -> str | None:
    """Return a valid access token (cached), refreshing via the refresh token. None if unconfigured."""
    if not configured():
        return None
    now = time.time()
    if _token_cache["value"] and float(_token_cache["expires"]) > now + 60:
        return str(_token_cache["value"])
    try:
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as c:
            r = await c.post(
                TOKEN_URL,
                data={
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "refresh_token": settings.google_refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            r.raise_for_status()
            data = r.json()
        token = data.get("access_token")
        if not token:
            return None
        _token_cache["value"] = token
        _token_cache["expires"] = now + float(data.get("expires_in", 3600))
        return token
    except Exception as e:  # noqa: BLE001
        logger.warning(f"google token refresh failed: {type(e).__name__}: {e}")
        return None


async def api_get(url: str, params: dict | None = None) -> dict:
    token = await access_token()
    if not token:
        raise RuntimeError("google not authorized")
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as c:
        r = await c.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return r.json()


async def api_post(url: str, json: dict) -> dict:
    token = await access_token()
    if not token:
        raise RuntimeError("google not authorized")
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as c:
        r = await c.post(url, json=json, headers={"Authorization": f"Bearer {token}"})
        r.raise_for_status()
        return r.json()
