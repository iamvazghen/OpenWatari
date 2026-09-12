"""Read a day's calendar and name what is wrong with it (21.F2).

Afon could already list events and warn that one was imminent. What he could not do is look at the
day as a whole and say "this will not work" — the three ways a day is broken before it starts:

    overlap    two commitments at the same time. One of them is not happening.
    travel     back-to-back commitments in different places with no time to get between them.
    no-break   a run of hours with nothing between them, which is how a day gets eaten and
               afterwards nobody can say where it went.

All three are arithmetic on a sorted list. No model is consulted, which is not a shortcut: a
non-deterministic answer to "are you double-booked" is worse than no answer, because the owner
cannot tell a hallucinated clash from a real one, and checking costs him the time the check was
meant to save.

ponytail: pairwise comparison, O(n^2) on one day's events. A sweep line would be the right answer
at a thousand events; at the dozen a person actually has, it would be more code doing the same
thing more slowly to read.

    uv run python -m afon.brain.schedule     # self-check
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

#: Minutes assumed necessary to get between two named places. Deliberately one number rather than a
#: distance lookup: a maps API would turn a local arithmetic check into a network call on the
#: proactive tick, and the owner knows better than any API whether his two Tuesday locations are
#: across the hall or across the city — this is the knob he turns when they are not.
TRAVEL_MINUTES = 20

#: A gap at or above this counts as a real break. Below it, the next thing has already started.
BREAK_MINUTES = 15

#: How long an unbroken run may get before it is worth saying so out loud.
MAX_RUN_MINUTES = 180


@dataclass(frozen=True)
class Clash:
    kind: str           # "overlap" | "travel" | "no-break"
    message: str
    events: tuple[str, ...]


def _parse(ev: dict) -> tuple[datetime, datetime, str, str] | None:
    """(start, end, summary, location) for a TIMED event; None for all-day or unparseable.

    All-day events are skipped on purpose. They carry a date and no clock, so every clash rule here
    would either have to invent a time for them or treat them as filling the day — and "you are
    double-booked" is a bad thing to say because someone's birthday is in the calendar.
    """
    try:
        s = (ev.get("start") or {}).get("dateTime")
        e = (ev.get("end") or {}).get("dateTime")
        if not s or not e:
            return None
        start = datetime.fromisoformat(s.replace("Z", "+00:00"))
        end = datetime.fromisoformat(e.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if end <= start:
        return None
    return start, end, str(ev.get("summary") or "(untitled)"), str(ev.get("location") or "").strip()


def clashes(events: list[dict], *, travel_minutes: int = TRAVEL_MINUTES,
            max_run_minutes: int = MAX_RUN_MINUTES) -> list[Clash]:
    """Every structural problem in a day's events, most disruptive first."""
    parsed = sorted((p for p in (_parse(e) for e in events) if p), key=lambda p: (p[0], p[1]))
    out: list[Clash] = []

    for i, (s1, e1, t1, l1) in enumerate(parsed):
        for s2, e2, t2, l2 in parsed[i + 1:]:
            if s2 >= e1:
                break  # sorted by start: nothing later can overlap this one either
            out.append(Clash(
                "overlap",
                f"'{t1}' and '{t2}' are booked at the same time, sir — "
                f"{s2:%H:%M} to {min(e1, e2):%H:%M} is double-booked.",
                (t1, t2)))

    for (s1, e1, t1, l1), (s2, e2, t2, l2) in zip(parsed, parsed[1:]):
        gap = (s2 - e1).total_seconds() / 60
        if l1 and l2 and l1.lower() != l2.lower() and 0 <= gap < travel_minutes:
            out.append(Clash(
                "travel",
                f"'{t1}' at {l1} ends {int(gap)} minutes before '{t2}' starts at {l2}, sir. "
                f"That is not enough time to get between them.",
                (t1, t2)))

    out.extend(_runs(parsed, max_run_minutes))
    order = {"overlap": 0, "travel": 1, "no-break": 2}
    return sorted(out, key=lambda c: order[c.kind])


def _runs(parsed: list[tuple[datetime, datetime, str, str]], max_run_minutes: int) -> list[Clash]:
    """Unbroken stretches longer than the limit, as one clash each."""
    out: list[Clash] = []
    if not parsed:
        return out
    run = [parsed[0]]
    for item in parsed[1:] + [None]:  # the None flushes the final run
        if item is not None:
            gap = (item[0] - max(r[1] for r in run)).total_seconds() / 60
            if gap < BREAK_MINUTES:
                run.append(item)
                continue
        span = (max(r[1] for r in run) - run[0][0]).total_seconds() / 60
        if len(run) > 1 and span >= max_run_minutes:
            out.append(Clash(
                "no-break",
                f"You have {int(span // 60)} hours back to back from {run[0][0]:%H:%M}, sir, "
                f"with nothing between them. {len(run)} commitments and no break.",
                tuple(r[2] for r in run)))
        run = [item] if item is not None else []
    return out


def spoken(found: list[Clash]) -> str:
    """One line for the day. Empty list means the day holds together, and says so."""
    if not found:
        return "Your day holds together, sir — nothing overlapping, and room to move between things."
    return " ".join(c.message for c in found)


def _selfcheck() -> None:
    def ev(summary, start, end, location=""):
        d = {"summary": summary,
             "start": {"dateTime": f"2026-09-14T{start}:00+02:00"},
             "end": {"dateTime": f"2026-09-14T{end}:00+02:00"}}
        if location:
            d["location"] = location
        return d

    clean = [ev("standup", "09:00", "09:15"), ev("lunch", "12:30", "13:30")]
    assert clashes(clean) == [], clashes(clean)
    assert "holds together" in spoken([])

    double = clashes([ev("dentist", "10:00", "11:00"), ev("review", "10:30", "11:30")])
    assert [c.kind for c in double] == ["overlap"], double
    assert "double-booked" in double[0].message
    assert double[0].events == ("dentist", "review")

    # Touching, not overlapping: 10:00-11:00 and 11:00-12:00 is a tight day, not a clash.
    assert not [c for c in clashes([ev("a", "10:00", "11:00"), ev("b", "11:00", "12:00")])
                if c.kind == "overlap"]

    travel = clashes([ev("client", "10:00", "11:00", "Cologne"),
                      ev("dentist", "11:05", "11:30", "Berlin")])
    assert any(c.kind == "travel" for c in travel), travel
    assert "not enough time" in next(c for c in travel if c.kind == "travel").message
    # Same place back-to-back is fine, and so is an unnamed location — inventing a journey between
    # two events whose location nobody recorded would be a warning about nothing.
    assert not [c for c in clashes([ev("a", "10:00", "11:00", "Office"),
                                    ev("b", "11:05", "11:30", "office")]) if c.kind == "travel"]
    assert not [c for c in clashes([ev("a", "10:00", "11:00"), ev("b", "11:05", "11:30")])
                if c.kind == "travel"]

    marathon = clashes([ev("a", "09:00", "10:00"), ev("b", "10:05", "11:00"),
                        ev("c", "11:00", "12:30"), ev("d", "12:35", "13:30")])
    runs = [c for c in marathon if c.kind == "no-break"]
    assert len(runs) == 1, marathon
    assert "no break" in runs[0].message and len(runs[0].events) == 4

    # A real gap splits the run, so a normal day with lunch in it is never flagged.
    assert not [c for c in clashes([ev("a", "09:00", "11:00"), ev("b", "12:00", "14:00")])
                if c.kind == "no-break"]

    # All-day and malformed events are skipped, never guessed at.
    allday = {"summary": "birthday", "start": {"date": "2026-09-14"}, "end": {"date": "2026-09-15"}}
    assert clashes([allday, ev("a", "10:00", "11:00")]) == []
    assert clashes([{"summary": "broken", "start": {"dateTime": "not a time"}, "end": {}}]) == []
    assert clashes([ev("backwards", "11:00", "10:00")]) == []
    assert clashes([]) == []

    # Ordering: the thing that makes a day impossible is said before the thing that makes it tiring.
    mixed = clashes([ev("a", "09:00", "12:00", "Berlin"), ev("b", "11:00", "13:00", "Cologne"),
                     ev("c", "13:00", "15:00", "Berlin")])
    assert mixed[0].kind == "overlap", [c.kind for c in mixed]

    print("schedule self-check OK")


if __name__ == "__main__":
    _selfcheck()
