"""Conflict-only interventions (Phase 4) — the one time Afon deliberately INTERRUPTS.

Every other proactive behaviour steps back when the owner is busy (Phase 1 holds routine nudges while
he's watching something). This is the single, narrow exception the owner asked for: if he's absorbed
in MEDIA *and* a real, timed commitment is about to start, Afon pauses the media and flags the
concrete conflict — "you've got the dentist in 10 minutes and you're mid-episode; head off, or keep
watching?". One clear question, one-word dismissible, and it rides the SAME dismissal-learning as any
other kind (kind ``conflict``), so if the owner keeps waving it off it backs off on its own.

Deliberately conservative — this only ever fires on a *named calendar event within the lead window
while media is actually playing*. No event, no media, not logged in, or tracking paused => nothing.
It is OFF by default (``interventions_enabled``); the owner arms it once he trusts the rest.

The intervention's side-effect (pausing the media) travels on the Signal's ``action``, which the
proactive engine runs only when it actually interjects — so the spoken "I've paused it" is truthful.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from loguru import logger

from afon.config import settings

# Media playing in a browser reads as category "browsing", not "media" — so we also look at the window
# TITLE for tell-tale streaming hosts / now-playing markers. Lowercased substring match.
_MEDIA_TITLE_CUES = (
    "youtube", "netflix", "twitch", "prime video", "disney+", "disney plus", "hbo", "max)",
    "hulu", "vimeo", "crunchyroll", "- watch", "now playing", "plex", "kinopoisk", "▶",
    "spotify", "soundcloud", "apple music", "movie", "s01e", "s02e", "episode",
)


def _looks_like_media(app: str, title: str, category: str) -> bool:
    """True if the foreground looks like the owner is consuming media right now."""
    if category == "media":                       # VLC / Spotify / a Netflix app / etc.
        return True
    blob = f"{app} {title}".lower()
    if category == "browsing" and any(c in blob for c in _MEDIA_TITLE_CUES):
        return True
    return False


def _media_context(now: float):
    """Return the current Snapshot iff the owner is actively watching/listening, else None."""
    from afon.brain.presence import PRESENCE, _category

    if not PRESENCE.enabled:
        return None
    snap = PRESENCE.current()
    if not PRESENCE._fresh(snap, now):            # stale read => we don't actually know
        return None
    if snap.idle >= settings.presence_idle_threshold_seconds:
        return None                               # away from the machine — nothing to interrupt
    if _looks_like_media(snap.app, snap.title, _category(snap.app)):
        return snap
    return None


def _what(snap) -> str:
    """A short noun for what he's mid-way through, from the window title."""
    blob = f"{snap.app} {snap.title}".lower()
    if any(m in blob for m in ("spotify", "soundcloud", "apple music", "music")):
        return "track"
    if any(m in blob for m in ("youtube", "netflix", "twitch", "prime video", "hbo", "hulu",
                               "episode", "movie", "s01e", "s02e")):
        return "video"
    return "media"


async def _imminent_events(lead_minutes: int) -> list[tuple[int, str, str]]:
    """Timed calendar events starting within ``lead_minutes`` — as (minutes_away, title, id).

    Fail-quiet: not logged in / API error / all-day-only => empty. Mirrors calendar_signals but with a
    configurable, tighter lead window (a conflict needs less runway than a generic heads-up)."""
    from afon.brain.google import api_get, configured

    if not configured():
        return []
    now = datetime.now(timezone.utc)
    try:
        data = await api_get(
            "https://www.googleapis.com/calendar/v3/calendars/primary/events",
            params={
                "timeMin": now.isoformat(),
                "timeMax": (now + timedelta(minutes=max(1, lead_minutes))).isoformat(),
                "singleEvents": "true", "orderBy": "startTime", "maxResults": 5,
            },
        )
    except Exception:  # noqa: BLE001 — a broken source must never throw into the tick
        return []
    out: list[tuple[int, str, str]] = []
    for ev in data.get("items") or []:
        start = (ev.get("start") or {}).get("dateTime")     # timed only; ignore all-day
        if not start:
            continue
        try:
            when = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            continue
        mins = max(0, round((when - now).total_seconds() / 60))
        out.append((mins, ev.get("summary", "an event"), str(ev.get("id") or start)))
    return out


def _conflict_message(snap, mins: int, title: str) -> str:
    what = _what(snap)
    when = "is starting now" if mins == 0 else f"starts in {mins} minute(s)"
    return (f"Sir — you're mid-{what}, but '{title}' {when}. I've paused it. "
            "Want to head off, or shall I let it run?")


async def _pause_action() -> None:
    """The side-effect carried by a conflict signal: pause the owner's media. Fail-quiet."""
    from afon.brain.tools.system import media_pause

    try:
        await media_pause({})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"intervention media pause failed: {type(e).__name__}: {e}")


async def conflict_signals() -> list:
    """Proactive source: at most one conflict intervention when media + an imminent commitment collide.

    Returns a Signal whose urgency clears the context-override (so the engine interrupts the media he's
    watching) and whose ``action`` pauses that media as it fires. Repeat-suppression (by event id) and
    the per-kind learning penalty keep it from nagging. Off unless ``interventions_enabled``."""
    from afon.brain.proactive import Signal  # lazy: avoid an import cycle

    if not settings.interventions_enabled:
        return []
    now = time.time()
    snap = _media_context(now)
    if snap is None:
        return []                                  # not watching anything -> never intervene
    events = await _imminent_events(settings.intervention_lead_minutes)
    if not events:
        return []                                  # no real commitment -> never intervene
    mins, title, eid = min(events, key=lambda e: e[0])   # the nearest conflict only
    return [Signal(
        key=f"conflict-{eid}",
        kind="conflict",
        urgency=settings.intervention_urgency,
        message=_conflict_message(snap, mins, title),
        action=_pause_action,
    )]
