"""Wearable vitals via Google Fit — sleep + activity, feeding the wellbeing/proactive engines.

Rides the SAME Google OAuth app as Gmail/Calendar (fitness scopes were added to the consent,
2026-07-28). Data appears once a phone/watch/Oura-bridge feeds Google Fit; until then (or if the
Fitness API isn't enabled in the Cloud project) both tools degrade to a calm spoken note.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from afon.brain.google import api_get, api_post, configured
from afon.brain.tools.base import not_configured, tool_error

_NEEDS = "the Google OAuth login (bench/google_login.py) + the Fitness API enabled in the Cloud project"
_BASE = "https://www.googleapis.com/fitness/v1/users/me"


async def sleep_summary(args: dict) -> str:
    """Last night's sleep from Google Fit sleep sessions (activityType 72)."""
    if not configured():
        return not_configured("Google Fit", _NEEDS)
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=int(args.get("hours_back", 24) or 24))
    try:
        data = await api_get(f"{_BASE}/sessions", {
            "activityType": 72,
            "startTime": start.isoformat().replace("+00:00", "Z"),
            "endTime": now.isoformat().replace("+00:00", "Z"),
        })
        sessions = data.get("session") or []
        if not sessions:
            return ("No sleep data in Google Fit for last night, sir — nothing has fed it yet "
                    "(needs a phone/watch sleep tracker syncing to Fit).")
        total_ms = sum(int(s["endTimeMillis"]) - int(s["startTimeMillis"]) for s in sessions)
        hrs, mins = divmod(total_ms // 60000, 60)
        last = max(sessions, key=lambda s: int(s["endTimeMillis"]))
        woke = datetime.fromtimestamp(int(last["endTimeMillis"]) / 1000, tz=timezone.utc)
        return f"You slept about {hrs}h{mins:02d}m, sir, waking around {woke.strftime('%H:%M')} UTC."
    except Exception as e:  # noqa: BLE001
        if "403" in str(e):
            return not_configured("Google Fit", _NEEDS)
        return tool_error("sleep summary", e)


async def activity_summary(args: dict) -> str:
    """Today's steps (+ average heart rate when a source provides it)."""
    if not configured():
        return not_configured("Google Fit", _NEEDS)
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    body = {
        "aggregateBy": [{"dataTypeName": "com.google.step_count.delta"},
                        {"dataTypeName": "com.google.heart_rate.bpm"}],
        "bucketByTime": {"durationMillis": 86400000},
        "startTimeMillis": int(start.timestamp() * 1000),
        "endTimeMillis": int(now.timestamp() * 1000),
    }
    try:
        data = await api_post(f"{_BASE}/dataset:aggregate", body)
        steps = 0
        hr_vals: list[float] = []
        for bucket in data.get("bucket") or []:
            for ds in bucket.get("dataset") or []:
                for p in ds.get("point") or []:
                    t = p.get("dataTypeName", "")
                    for v in p.get("value") or []:
                        if "step_count" in t and "intVal" in v:
                            steps += v["intVal"]
                        elif "heart_rate" in t and "fpVal" in v:
                            hr_vals.append(v["fpVal"])
        if not steps and not hr_vals:
            return ("No activity data in Google Fit today, sir — nothing has fed it yet "
                    "(needs a phone/watch syncing to Fit).")
        out = f"About {steps:,} steps today, sir"
        if hr_vals:
            out += f"; average heart rate {sum(hr_vals) / len(hr_vals):.0f} bpm"
        return out + "."
    except Exception as e:  # noqa: BLE001
        if "403" in str(e):
            return not_configured("Google Fit", _NEEDS)
        return tool_error("activity summary", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "sleep_summary",
            "description": ("How the owner SLEPT last night (duration, wake time) from their "
                            "wearable via Google Fit — 'how did I sleep', wellbeing checks."),
            "parameters": {"type": "object", "properties": {
                "hours_back": {"type": "integer", "description": "Lookback window (default 24h)."}},
                "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "activity_summary",
            "description": ("The owner's steps and average heart rate TODAY from their wearable via "
                            "Google Fit. Use for 'how active was I', 'how many steps', "
                            "training-load checks. For last night's rest use sleep_summary."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

HANDLERS = {"sleep_summary": sleep_summary, "activity_summary": activity_summary}
