"""Calendar date handling — the three bugs found live on 2026-07-30, all of which reported success:

  1. "what's on tomorrow?" answered about TODAY. list_events could only express "N days forward from
     now", so a named day was unreachable.
  2. create_event 400'd on every seconds-less time ('2026-06-12T15:00') — the exact shape our own
     schema told the LLM to send, so "put gym in my calendar at three" always failed.
  3. Times were spoken as "2026-07-31 at 14" — the raw ISO string sliced to 16 chars, cutting the
     clock in half.

Offline: the Google call is stubbed, so this asserts OUR request-building and phrasing, not theirs.
"""
from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import afon.brain.tools.calendar as cal
from afon.config import settings

PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label} {detail}")


async def main() -> None:
    tz = ZoneInfo(settings.user_tz)
    today = datetime.now(tz).date()
    tomorrow = today + timedelta(days=1)
    sent: dict = {}

    async def fake_get(url, params=None):
        sent.clear()
        sent.update(params or {})
        return {"items": []}

    async def fake_post(url, json):
        sent.clear()
        sent["url"] = url
        sent["body"] = json
        return {"id": "x"}

    cal.api_get, cal.api_post = fake_get, fake_post
    cal.configured = lambda: True

    # --- 1) a named day queries THAT day, in the owner's timezone, and is named back ------------
    out = await cal.list_events({"date": tomorrow.isoformat()})
    check("a date query says 'tomorrow', not 'today'", "tomorrow" in out, out)
    start = datetime.fromisoformat(sent["timeMin"])
    end = datetime.fromisoformat(sent["timeMax"])
    check("window starts at local midnight of that day",
          start.date() == tomorrow and start.hour == 0 and start.utcoffset() == tz.utcoffset(start),
          str(start))
    check("window is exactly one day long", end - start == timedelta(days=1), str(end - start))

    out = await cal.list_events({"date": (today + timedelta(days=4)).isoformat()})
    check("a further-out day is named by weekday, not 'today'",
          "today" not in out and "tomorrow" not in out, out)

    check("an unparseable date is refused, not silently treated as today",
          "couldn't read" in await cal.list_events({"date": "next tuesday"}))

    # A plain window query still works (unchanged behaviour).
    await cal.list_events({"days": 7})
    span = datetime.fromisoformat(sent["timeMax"]) - datetime.fromisoformat(sent["timeMin"])
    check("days=7 still asks for a 7-day window", span == timedelta(days=7), str(span))

    # The imminent-event window the prep signal needs. It used to pass from/to/limit — names
    # list_events never accepted — so it read a whole DAY and called everything "starting soon".
    await cal.list_events({"minutes": 30})
    span = datetime.fromisoformat(sent["timeMax"]) - datetime.fromisoformat(sent["timeMin"])
    check("minutes=30 asks for a 30-minute window", span == timedelta(minutes=30), str(span))

    # --- 2) seconds are added before the request reaches Google --------------------------------
    check("_rfc3339 adds seconds", cal._rfc3339("2026-06-12T15:00") == "2026-06-12T15:00:00")
    check("_rfc3339 leaves a full timestamp alone",
          cal._rfc3339("2026-06-12T15:00:00") == "2026-06-12T15:00:00")
    check("_rfc3339 passes garbage through for the API to reject", cal._rfc3339("soon") == "soon")

    await cal.create_event({"summary": "gym", "start": f"{tomorrow.isoformat()}T15:00"})
    check("create sends a seconds-bearing start",
          sent["body"]["start"]["dateTime"].count(":") == 2, sent["body"]["start"]["dateTime"])
    check("create defaults to a one-hour block",
          sent["body"]["end"]["dateTime"] == f"{tomorrow.isoformat()}T16:00:00",
          sent["body"]["end"]["dateTime"])

    # --- 3) times are speakable, never a sliced ISO string -------------------------------------
    said = cal._fmt_when({"start": {"dateTime": f"{tomorrow.isoformat()}T14:00:00"}})
    check("a time is spoken as 'tomorrow at 14:00'", said == "tomorrow at 14:00", said)
    said_today = cal._fmt_when({"start": {"dateTime": f"{today.isoformat()}T09:05:00"}})
    check("today's events say 'today at 09:05'", said_today == "today at 09:05", said_today)
    said_allday = cal._fmt_when({"start": {"date": tomorrow.isoformat()}})
    check("an all-day event speaks no clock",
          "at" not in said_allday.replace("at ", "", 0) or ":" not in said_allday, said_allday)
    # Look for the ISO separator specifically: a bare `"T" not in s` also matches the T in the
    # weekday names "Tue" and "Thu", so this check failed on two days out of every seven and
    # passed on the other five. Caught on 2026-08-10, when tomorrow was a Tuesday and the all-day
    # format was the entirely correct "on Tue 11 August". A date-dependent test is worse than no
    # test: it teaches you to expect a red run rather than to read it.
    iso_sep = re.compile(r"\dT\d")
    check("no spoken time is ever a truncated ISO string",
          all(not iso_sep.search(s) and not s.endswith(" at 14")
              for s in (said, said_today, said_allday)),
          f"{said!r} {said_today!r} {said_allday!r}")

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
