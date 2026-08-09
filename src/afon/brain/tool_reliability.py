"""Tool-reliability learning — Afon learns which of his own tools have been flaky lately (Tools-util).

The audit log (``audit.py``) already records every tool call with an ``ok`` flag, one JSON line per
call across daily files. This reads that trail back and aggregates a per-tool success rate over a recent
window, so the assistant can *learn from its own behaviour* instead of treating every tool as equally
trustworthy every time:

  * ``reliability(days)`` — {tool: {calls, failures, rate}} over the last ``days`` of audit files.
  * ``flaky_note()`` — a per-turn system note naming tools that have been unreliable *recently* (enough
    calls to matter, low success rate), so the model sets expectations / prefers an alternative. ``None``
    when everything's healthy — like ``manner_note``, silent unless there's something to say. Cached with a
    short TTL so it costs one file-scan every few minutes, not one per turn.
  * ``reliability_summary()`` — the worst offenders, for the ambient HUD.

ponytail: no new datastore — this is a read-only projection of the audit jsonl the brain already writes.
The window is small (7 days) and files are one-per-day, so a scan is cheap; the TTL cache makes the
per-turn path effectively free.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

from loguru import logger

from afon.brain.audit import _audit_dir

# A tool needs at least this many recent calls before a low rate means anything (one failure out of two
# is noise; three failures out of five is a pattern). And "flaky" = success rate at or below this.
_MIN_CALLS = 4
_FLAKY_RATE = 0.6

_CACHE: dict[str, object] = {"note": None, "at": 0.0}
_TTL_S = 300  # recompute the flaky note at most once every 5 min


def reliability(days: int = 7) -> dict[str, dict]:
    """Aggregate per-tool {calls, failures, rate} from the last ``days`` of audit files."""
    d = _audit_dir()
    if not d.is_dir():
        return {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    agg: dict[str, list[int]] = {}   # tool -> [calls, failures]
    for f in sorted(d.glob("*.jsonl")):
        try:
            fday = datetime.strptime(f.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if fday < cutoff:
            continue
        for ln in f.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                row = json.loads(ln)
            except json.JSONDecodeError:
                continue
            tool = row.get("tool")
            if not tool:
                continue
            slot = agg.setdefault(tool, [0, 0])
            slot[0] += 1
            if not row.get("ok", True):
                slot[1] += 1
    return {
        t: {"calls": c, "failures": fz, "rate": round((c - fz) / c, 2) if c else 1.0}
        for t, (c, fz) in agg.items()
    }


def _flaky_tools(stats: dict[str, dict]) -> list[tuple[str, dict]]:
    """Tools with enough calls to judge and a success rate at/below the flaky threshold, worst first."""
    bad = [(t, s) for t, s in stats.items() if s["calls"] >= _MIN_CALLS and s["rate"] <= _FLAKY_RATE]
    return sorted(bad, key=lambda kv: kv[1]["rate"])


def flaky_note(*, now: float | None = None) -> str | None:
    """A per-turn system note about recently-flaky tools, or None. Cached with a short TTL."""
    now = now if now is not None else time.time()
    if now - float(_CACHE["at"]) < _TTL_S:  # type: ignore[arg-type]
        return _CACHE["note"]  # type: ignore[return-value]
    try:
        bad = _flaky_tools(reliability())
    except Exception as e:  # noqa: BLE001 — a hint must never break a turn
        logger.debug(f"tool_reliability.flaky_note failed ({type(e).__name__})")
        bad = []
    if not bad:
        note = None
    else:
        parts = [f"{t} ({int(s['rate'] * 100)}% lately)" for t, s in bad[:3]]
        note = ("Reliability note (from your own recent tool history): these tools have been unreliable "
                f"recently — {', '.join(parts)}. Prefer a more dependable path if one exists, and if you "
                "must use one, tell him plainly it may not work rather than promising it will.")
    _CACHE["note"] = note
    _CACHE["at"] = now
    return note


def reliability_summary(limit: int = 5) -> list[dict]:
    """The worst offenders for the HUD: [{tool, calls, rate}], worst first (empty when all healthy)."""
    try:
        bad = _flaky_tools(reliability())
    except Exception:  # noqa: BLE001
        return []
    return [{"tool": t, "calls": s["calls"], "rate": s["rate"]} for t, s in bad[:limit]]


if __name__ == "__main__":  # smoke against the live audit trail
    print(json.dumps(reliability(), indent=2))
    print("flaky note:", flaky_note())
