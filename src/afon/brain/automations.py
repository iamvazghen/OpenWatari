"""One list of everything Afon runs on his own, with when it last ran and how it went (29.F4).

Afon runs three unrelated kinds of automation and each one knew only about itself. Background loops
had a registry with ticks and errors (31.F4). Scheduled reminders lived in APScheduler and knew
their next fire time. Macros were a file of steps that recorded nothing at all — run one on Monday
and by Friday there was no way to tell whether it had run, or worked.

So "what do you run for me, and is any of it broken" had no answer. Three surfaces each said a
third of it, in three different shapes, and the owner had to hold the join in his head. That is the
same failure as an automation that silently stopped: the information exists and nobody can reach it.

Two things this refuses to do, because both would make the list worse than no list:

  * **It never leaves a blank where it does not know.** A macro that has never run says so. A
    reminder whose last run nobody recorded says that, rather than showing an empty cell the owner
    reads as "never".
  * **It never implies a next run that does not exist.** A macro fires when he asks for it. Putting
    a guessed time in that column would turn a list he checks into a list he stops trusting.

    uv run python -m afon.brain.automations     # self-check
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from loguru import logger

#: A row with nothing behind it. Said out loud rather than left empty — see the module docstring.
NEVER = "never run"
ON_REQUEST = "when you ask"
UNKNOWN = "not recorded"


@dataclass(frozen=True)
class Automation:
    name: str
    kind: str                 # "loop" | "reminder" | "macro"
    what: str
    last_run: str             # human phrase, or NEVER / UNKNOWN
    next_run: str             # human phrase, or ON_REQUEST / UNKNOWN
    outcome: str              # "ok" | a failure sentence | NEVER
    healthy: bool = True
    detail: dict = field(default_factory=dict)


def _ago(ts: float | None, now: float) -> str:
    if not ts:
        return NEVER
    secs = max(0.0, now - ts)
    if secs < 90:
        return f"{int(secs)}s ago"
    if secs < 5400:
        return f"{int(secs / 60)} min ago"
    if secs < 172800:
        return f"{int(secs / 3600)}h ago"
    return f"{int(secs / 86400)}d ago"


def _from_loops(now: float) -> list[Automation]:
    try:
        from afon.brain import loops
    except Exception as e:  # noqa: BLE001
        logger.debug(f"automations: loop registry unavailable ({type(e).__name__}: {e})")
        return []
    out: list[Automation] = []
    for r in loops.snapshot(now):
        if r["never_ran"]:
            outcome, healthy = NEVER, True   # a fresh process has run nothing yet; not a fault
        elif r["last_error"]:
            outcome, healthy = f"failed: {r['last_error']}", False
        elif r["stale"]:
            outcome, healthy = f"no tick in {int(r['last_age_s'])}s", False
        else:
            outcome, healthy = "ok", True
        period = r["period_s"]
        if not period:
            # An event-driven listener has no next tick, and inventing one would be a lie the
            # owner could check. It runs when something arrives.
            nxt = "when something arrives"
        elif r["last_tick"]:
            nxt = f"in {max(0, int(r['last_tick'] + period - now))}s"
        else:
            nxt = f"every {int(period)}s"
        out.append(Automation(r["name"], "loop", r["what"], _ago(r["last_tick"], now), nxt,
                              outcome, healthy, {"runs": r["runs"], "errors": r["errors"]}))
    return out


def _from_reminders() -> list[Automation]:
    try:
        from afon.brain.scheduler import SCHEDULER

        rows = SCHEDULER.list_reminders()
    except Exception as e:  # noqa: BLE001
        logger.debug(f"automations: scheduler unavailable ({type(e).__name__}: {e})")
        return []
    # APScheduler keeps no run history, so the last-run column is UNKNOWN rather than blank. That
    # is the honest answer: "I don't record it" is a different fact from "it never fired", and
    # showing the second when the first is true is how a status list starts lying.
    return [Automation(name or job_id, "reminder", "a scheduled reminder", UNKNOWN, nxt, UNKNOWN)
            for job_id, name, nxt in rows]


def _from_macros(now: float) -> list[Automation]:
    try:
        from afon.brain.tools.macros import _load
    except Exception as e:  # noqa: BLE001
        logger.debug(f"automations: macro store unavailable ({type(e).__name__}: {e})")
        return []
    out: list[Automation] = []
    for name in sorted(_load()):
        m = _load()[name]
        last = m.get("last_run")
        outcome = str(m.get("last_outcome") or (NEVER if not last else "ok"))
        out.append(Automation(
            name, "macro", (m.get("description") or f"{len(m.get('steps') or [])} steps").strip(),
            _ago(last, now), ON_REQUEST, outcome, outcome in ("ok", NEVER),
            {"steps": len(m.get("steps") or [])}))
    return out


def automations(now: float | None = None) -> list[Automation]:
    """Everything Afon runs on his own, one shape, worst first so a failure is never below a fold."""
    now = now if now is not None else time.time()
    rows = _from_loops(now) + _from_reminders() + _from_macros(now)
    return sorted(rows, key=lambda a: (a.healthy, a.kind, a.name))


def spoken(rows: list[Automation] | None = None) -> str:
    """One paragraph. Leads with what is wrong, because that is the reason to ask."""
    rows = automations() if rows is None else rows
    if not rows:
        return "I'm not running anything on my own right now, sir."
    broken = [r for r in rows if not r.healthy]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.kind] = counts.get(r.kind, 0) + 1
    tally = ", ".join(f"{n} {k}{'s' if n != 1 else ''}" for k, n in sorted(counts.items()))
    head = f"I'm running {tally}, sir."
    if not broken:
        return head + " All of them are behaving."
    lines = [f"{r.name} ({r.kind}) — {r.outcome}, last run {r.last_run}" for r in broken[:5]]
    return head + f" {len(broken)} need your attention: " + "; ".join(lines) + "."


def _selfcheck() -> None:
    now = 1_000_000.0
    rows = [
        Automation("ticker", "loop", "fires reminders", "3 min ago", "in 40s", "ok"),
        Automation("morning", "macro", "lights and brief", NEVER, ON_REQUEST, NEVER),
        Automation("backup", "loop", "nightly backup", "2d ago", "in 60s",
                   "failed: disk full", False),
    ]
    ordered = sorted(rows, key=lambda a: (a.healthy, a.kind, a.name))
    assert ordered[0].name == "backup", [r.name for r in ordered]

    said = spoken(rows)
    assert "backup" in said and "disk full" in said, said
    assert "1 need" in said, said
    assert "behaving" in spoken([rows[0]]), spoken([rows[0]])
    assert "not running anything" in spoken([])

    assert _ago(None, now) == NEVER
    assert _ago(now - 30, now) == "30s ago"
    assert _ago(now - 600, now) == "10 min ago"
    assert _ago(now - 7200, now) == "2h ago"
    assert _ago(now - 3 * 86400, now) == "3d ago"

    # The live registry must produce rows without a running brain: a status surface that only works
    # in production is a status surface nobody tests.
    live = automations()
    assert all(isinstance(r, Automation) for r in live)
    assert all(r.last_run and r.next_run and r.outcome for r in live), live[:3]
    print(f"automations self-check OK ({len(live)} live rows)")


if __name__ == "__main__":
    _selfcheck()
