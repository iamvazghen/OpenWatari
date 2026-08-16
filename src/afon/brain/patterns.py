"""User behaviour pattern recognition.

Afon logs every command's topic + timestamp, and on a daily cron (see ``scheduler.py``) it
scans the rolling log for repeating patterns: same time-of-day on N consecutive days, same
day-of-week on N consecutive weeks, or a topic the user asks about >=3 times a week. When a
pattern is detected it's durably written as an L1 fact so the proactive engine and the LLM
can recall it.

Patterns are intentionally coarse — daily human behaviour has plenty of noise, and a false
positive ("user plays Mr Blue Sky on Fridays") is forgivable since the owner can `forget`
the bad fact. Over-detecting is the right default.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

from afon.config import settings


# Where the rolling log lives. One JSON line per command, append-only.
def _log_path() -> Path:
    base = Path.home() / ".afon"
    p = base / "patterns.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def record(utterance: str, topic: str | None = None, when: datetime | None = None) -> None:
    """Append one entry to the rolling log. Called by the agent after every turn."""
    if not utterance or not utterance.strip():
        return
    when = when or datetime.now(timezone.utc)
    topic = (topic or _infer_topic(utterance)).strip().lower()
    if not topic:
        return
    try:
        with _log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": when.isoformat(timespec="seconds"),
                "topic": topic,
                "sample": utterance.strip()[:80],
            }) + "\n")
    except OSError as e:
        logger.debug(f"patterns.record: write failed ({e})")


_TOPIC_KEYWORDS = (
    "lofi", "music", "video", "youtube", "channel",
    "email", "gmail", "inbox", "calendar", "event", "meeting",
    "task", "todo", "reminder",
    "weather", "news",
    "rabbit", "farm", "cologne",
    "vault", "note", "remember",
    "github", "open", "search", "play", "send",
)


def _infer_topic(utterance: str) -> str:
    low = utterance.lower()
    for kw in _TOPIC_KEYWORDS:
        if kw in low:
            return kw
    return ""


def _read_window(days: int = 30) -> list[dict]:
    """Read entries from the last `days` days (graceful: missing file = empty)."""
    p = _log_path()
    if not p.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[dict] = []
    try:
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    ts = datetime.fromisoformat(entry["ts"])
                except (KeyError, ValueError):
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    out.append(entry)
    except OSError as e:
        logger.debug(f"patterns._read_window: read failed ({e})")
    return out


def detect(lookback_days: int = 30, min_occurrences: int = 3) -> list[str]:
    """Detect repeating patterns in the rolling log. Returns human-readable facts.

    Heuristics (broad on purpose):
      1. Topic + hour-of-day: user mentions topic N times in the same UTC hour window across days.
      2. Topic + day-of-week: user mentions topic N times on the same weekday across weeks.
      3. Topic volume: any topic mentioned >=8 times in lookback_days = "frequently asked".

    ``min_occurrences`` defaults to 3 so daily-drive noise doesn't surface; bump to 2 in tests.
    """
    entries = _read_window(days=lookback_days)
    if not entries:
        return []
    facts: list[str] = []
    # Heuristic 1: topic × hour-of-day
    by_topic_hour: dict[str, Counter] = defaultdict(Counter)
    for e in entries:
        try:
            ts = datetime.fromisoformat(e["ts"])
        except (KeyError, ValueError):
            continue
        topic = (e.get("topic") or "").strip()
        if not topic:
            continue
        by_topic_hour[topic][ts.hour] += 1
    for topic, hours in by_topic_hour.items():
        hot_hour, hot_count = hours.most_common(1)[0]
        if hot_count >= min_occurrences:
            facts.append(
                f"user often mentions '{topic}' around {hot_hour:02d}:00 UTC "
                f"({hot_count} times in last {lookback_days} days)"
            )
    # Heuristic 2: topic × day-of-week
    by_topic_dow: dict[str, Counter] = defaultdict(Counter)
    for e in entries:
        try:
            ts = datetime.fromisoformat(e["ts"])
        except (KeyError, ValueError):
            continue
        topic = (e.get("topic") or "").strip()
        if not topic:
            continue
        by_topic_dow[topic][ts.weekday()] += 1
    dow_names = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"]
    for topic, dows in by_topic_dow.items():
        best_dow, best_count = dows.most_common(1)[0]
        if best_count >= min_occurrences:
            facts.append(
                f"user often mentions '{topic}' on {dow_names[best_dow]} "
                f"({best_count} times in last {lookback_days} days)"
            )
    # Heuristic 3: total volume
    topic_count = Counter((e.get("topic") or "").strip() for e in entries if e.get("topic"))
    for topic, c in topic_count.most_common(5):
        if c >= max(8, min_occurrences * 3):
            facts.append(f"user asks about '{topic}' frequently ({c} times in last {lookback_days} days)")
    # Deduplicate (heuristics can fire for the same topic).
    seen = set()
    deduped = []
    for f in facts:
        if f in seen:
            continue
        seen.add(f)
        deduped.append(f)
    return deduped


def persist_as_l1(store, lookback_days: int = 30) -> list[str]:
    """Detect patterns + write them as L1 learned facts. Returns the new facts added."""
    fresh = detect(lookback_days=lookback_days)
    if not fresh:
        return []
    added: list[str] = []
    for f in fresh:
        try:
            # MemoryStore.remember dedups by normalised text.
            store.remember(f, tags=["pattern"], source="pattern")
            added.append(f)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"patterns.persist_as_l1: remember({f[:50]!r}) failed: {e}")
    if added:
        logger.info(f"patterns: wrote {len(added)} new pattern fact(s)")
    return added