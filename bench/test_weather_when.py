"""H2.11 — `weather` must answer the question that was asked, not today's conditions.

"what's the weather in Berlin tomorrow" used to request only `current=` from open-meteo, so the
owner was told TODAY's temperature — confidently, in a full sentence, with no hint it was the
wrong day. Steering by description had already been tried and failed (the model still picks
`weather`, and `forecast` is in `_READ_INTENT_PATTERNS` so a tool call is forced regardless).

Hermetic: `http_get` is stubbed, so this asserts the REQUEST shape and the day arithmetic without
touching the network. The day arithmetic is the part worth pinning — reading index 0 for
"tomorrow" would reproduce the original bug exactly while still looking like a forecast.

    uv run python bench/test_weather_when.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from afon.brain.tools import utility  # noqa: E402

_ok = 0
_fail = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"PASS  {label}" + (f"  [{detail}]" if detail else ""))
    else:
        _fail += 1
        print(f"FAIL  {label}" + (f"  [{detail}]" if detail else ""))


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


# Distinct values per day so a wrong index is visible in the sentence rather than plausible.
DAILY = {
    "time": ["2026-08-09", "2026-08-10", "2026-08-11"],
    "temperature_2m_max": [11.0, 22.0, 33.0],
    "temperature_2m_min": [1.0, 2.0, 3.0],
    "precipitation_probability_max": [5, 90, 10],
    "weather_code": [0, 61, 3],
}

seen: list[dict] = []


async def _stub_http_get(url, params=None, **kw):
    seen.append({"url": url, "params": params or {}})
    if "geocoding" in url:
        return _Resp({"results": [{"name": "Berlin", "country": "Germany",
                                   "latitude": 52.5, "longitude": 13.4}]})
    return _Resp({"current": {"temperature_2m": 99.0, "apparent_temperature": 98.0,
                              "wind_speed_10m": 7.0, "weather_code": 0},
                  "daily": DAILY})


async def main() -> None:
    saved = utility.http_get
    utility.http_get = _stub_http_get
    # The cache would serve one mode's answer for another; clear between calls so each is a real
    # round trip. (That the key carries the mode at all is asserted at the end.)
    try:
        seen.clear()
        now = await utility.weather({"location": "Berlin"})
        check("99" in now, "no `when` still answers CURRENT conditions", now)
        check(any("current" in c["params"] for c in seen if "forecast" in c["url"]),
              "...and requests `current=` from open-meteo")

        seen.clear()
        utility.CACHE._local.clear()
        tom = await utility.weather({"location": "Berlin", "when": "tomorrow"})
        # 22.0 is index 1. 11.0 (index 0) would be the original bug wearing a forecast costume.
        check("22" in tom and "11" not in tom,
              "'tomorrow' reads day INDEX 1, not today", tom)
        check("90%" in tom, "...and includes tomorrow's rain probability", tom)
        fc = [c for c in seen if "forecast" in c["url"]][0]
        check("daily" in fc["params"] and fc["params"].get("forecast_days") == 2,
              "...requesting `daily=` with just the 2 days it needs", str(fc["params"]))

        seen.clear()
        utility.CACHE._local.clear()
        wk = await utility.weather({"location": "Berlin", "when": "this week"})
        check("33" in wk, "'this week' spans the whole range (high 33)", wk)
        check("90" in wk, "...and calls out the wettest day", wk)
        fc = [c for c in seen if "forecast" in c["url"]][0]
        check(fc["params"].get("forecast_days") == 7, "...requesting 7 days",
              str(fc["params"].get("forecast_days")))

        # Free-text `when`, because the model writes what the owner actually said.
        for phrase, want in (("right now", "current"), ("Tomorrow morning", "tomorrow"),
                             ("over the next few days", "week"), ("", "current")):
            check(utility._when_mode(phrase) == want, f"_when_mode({phrase!r}) -> {want}",
                  utility._when_mode(phrase))

        # THE CACHE TRAP: a key without the mode serves today's answer to "tomorrow" for 15
        # minutes, which is the original bug with a 900s fuse.
        utility.CACHE._local.clear()
        a = await utility.weather({"location": "Berlin"})
        b = await utility.weather({"location": "Berlin", "when": "tomorrow"})
        check(a != b, "the cache key carries the mode (today's answer is not reused for tomorrow)",
              f"{a[:28]!r} vs {b[:28]!r}")
    finally:
        utility.http_get = saved
        utility.CACHE._local.clear()


asyncio.run(main())

print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
