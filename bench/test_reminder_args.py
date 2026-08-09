"""set_reminder robustness: tolerate the arg shapes different models emit, so a reminder never fails on
formatting. Repro of the Proactivity drop — a fallback model put a relative phrase in `at`
('in 90 minutes'), which the old code fed to datetime.fromisoformat -> ValueError -> 'I couldn't set it'.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.tools.reminders import _normalize_reminder_args, _rel_to_minutes, set_reminder  # noqa: E402

_ok = _fail = 0


def check(cond, label):
    global _ok, _fail
    if cond:
        _ok += 1
    else:
        _fail += 1
        print(f"  FAIL: {label}")


# 1) relative-phrase parsing
check(_rel_to_minutes("in 90 minutes") == 90, "'in 90 minutes' -> 90")
check(_rel_to_minutes("2 hours") == 120, "'2 hours' -> 120")
check(_rel_to_minutes("in a while") is None, "non-delay phrase -> None")
check(_rel_to_minutes("in 1 day") == 1440, "'in 1 day' -> 1440")

# 2) arg-shape normalization — the message under alt keys
for k in ("message", "text", "reminder", "task", "about"):
    msg, *_ = _normalize_reminder_args({k: "stretch", "in_minutes": 20})
    check(msg == "stretch", f"message read from '{k}'")

# 3) numeric-string delay coerces
_, im, _, _ = _normalize_reminder_args({"message": "x", "in_minutes": "90"})
check(im == 90.0, f"in_minutes '90' -> 90.0 (got {im})")

# 4) THE bug: a relative phrase in `at`/`when` folds into a delay (never reaches fromisoformat)
_, im, at, _ = _normalize_reminder_args({"message": "x", "at": "in 90 minutes"})
check(im == 90 and at is None, f"relative `at` -> in_minutes, at cleared (got im={im}, at={at!r})")
_, im, at, _ = _normalize_reminder_args({"text": "stretch", "when": "in 2 hours"})
check(im == 120 and at is None, f"relative `when` -> in_minutes (got im={im}, at={at!r})")

# 5) an ABSOLUTE ISO time is preserved for the scheduler
_, im, at, _ = _normalize_reminder_args({"message": "x", "at": "2026-07-25T09:00:00"})
check(im is None and at == "2026-07-25T09:00:00", f"absolute `at` preserved (got im={im}, at={at!r})")


async def _live():
    # 6) end-to-end: the exact failing shape now succeeds (real scheduler)
    r = await set_reminder({"text": "stretch", "when": "in 90 minutes"})
    check(r.startswith("Done, sir"), f"the previously-failing arg shape now sets a reminder (got {r!r})")


asyncio.run(_live())
print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
