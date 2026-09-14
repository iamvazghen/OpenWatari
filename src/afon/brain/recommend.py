"""15.F1-F3 — one domain, one explicit reason, and a log that makes learning possible later.

The plan's instruction is unusually specific and it is right: **log first**. A recommender trained
on a handful of events is a random number generator with a confidence interval, so there is no
ranker here. There are rules the owner can read, and a decisions table so that in six months there
is something to train on, replay against, or argue with.

The domain is "what should I work on next", because it is the one where Afon already holds the
inputs: open to-dos with their deadlines and priorities, the objectives they serve, and whether the
next hour is free. Restaurants and reading lists need taste he has no data for, and inventing taste
is what 15.F3 forbids.

**The reason is not decoration.** Every recommendation names the rule that decided it and the data
that rule read — "this is overdue by two days" rather than "this seems important". A recommendation
whose reason cannot be checked is indistinguishable from a guess, and a guess delivered confidently
is worse than no answer: the owner reorganises his morning around it.

**And it declines.** No open work, or nothing that can be told apart, and the answer is that there
is no basis for a recommendation — not the first item in an arbitrary order presented as a choice.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from afon.shared.paths import state_dir

NEXT_TASK = "next-task"

#: Rules in the order they decide, strongest first. The name is what gets spoken, so it has to read
#: as a reason to a person rather than as a field name.
OVERDUE = "overdue"
DUE_SOON = "due soonest"
SERVES_OBJECTIVE = "serves an active objective"
PRIORITY = "highest priority"

#: A deadline this far out no longer distinguishes one task from another.
SOON_DAYS = 3.0


def _ledger() -> Path:
    return state_dir() / "recommendations.jsonl"


@dataclass(frozen=True)
class Recommendation:
    """What was suggested, why, and what else was in the running."""

    domain: str
    choice: str
    rule: str
    reason: str
    basis: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    key: str = ""
    at: float = field(default_factory=time.time)

    def row(self) -> dict:
        return {"domain": self.domain, "choice": self.choice, "rule": self.rule,
                "reason": self.reason, "basis": self.basis, "candidates": self.candidates,
                "key": self.key, "at": self.at}

    def spoken(self) -> str:
        others = ""
        if len(self.candidates) > 1:
            others = f" I looked at {len(self.candidates)} open items."
        return f"I'd do '{self.choice}' next, sir — {self.reason}.{others}"


@dataclass(frozen=True)
class NoBasis:
    """Why no recommendation was made. A first-class answer, not an empty one."""

    domain: str
    why: str

    def spoken(self) -> str:
        return f"I've no basis for a recommendation there, sir — {self.why}."


def _days_to(deadline: float | None, now: float) -> float | None:
    return None if deadline is None else (deadline - now) / 86400.0


def next_task(todos: list, *, active_objectives: set[str] | None = None,
              now: float | None = None) -> Recommendation | NoBasis:
    """What to work on next, from the owner's own open work. Rules, in a stated order.

    `todos` are the open to-dos; `active_objectives` the ids currently being driven. Both are
    passed in rather than fetched so this stays testable and so the caller owns the query budget.
    """
    now = now if now is not None else time.time()
    active = active_objectives or set()
    open_items = [t for t in todos if getattr(t, "status", "open") == "open"]
    if not open_items:
        return NoBasis(NEXT_TASK, "there's nothing open on your list")

    def serves(t) -> bool:
        return str((getattr(t, "meta", None) or {}).get("objective") or "") in active and bool(active)

    titles = [str(getattr(t, "title", "")) for t in open_items]

    overdue = [(t, _days_to(getattr(t, "deadline", None), now)) for t in open_items]
    overdue = [(t, d) for t, d in overdue if d is not None and d < 0]
    if overdue:
        t, d = min(overdue, key=lambda p: p[1])
        late = abs(d)
        when = f"{late * 24:.0f} hours" if late < 1 else f"{late:.0f} days"
        return Recommendation(
            NEXT_TASK, str(t.title), OVERDUE, f"it's overdue by {when}",
            basis=[f"deadline on '{t.title}' passed {when} ago"], candidates=titles,
            key=f"{NEXT_TASK}:{getattr(t, 'id', t.title)}")

    soon = [(t, _days_to(getattr(t, "deadline", None), now)) for t in open_items]
    soon = [(t, d) for t, d in soon if d is not None and d <= SOON_DAYS]
    if soon:
        t, d = min(soon, key=lambda p: p[1])
        when = f"{d * 24:.0f} hours" if d < 1 else f"{d:.0f} days"
        return Recommendation(
            NEXT_TASK, str(t.title), DUE_SOON, f"it's due in {when}, sooner than anything else open",
            basis=[f"deadline on '{t.title}' is {when} away"], candidates=titles,
            key=f"{NEXT_TASK}:{getattr(t, 'id', t.title)}")

    serving = [t for t in open_items if serves(t)]
    if serving:
        t = max(serving, key=lambda t: getattr(t, "prio_rank", 1))
        obj = (getattr(t, "meta", None) or {}).get("objective")
        return Recommendation(
            NEXT_TASK, str(t.title), SERVES_OBJECTIVE,
            f"it's the only open work on '{obj}'" if len(serving) == 1
            else f"it serves '{obj}', which you're actively driving",
            basis=[f"'{t.title}' is attributed to objective {obj}"], candidates=titles,
            key=f"{NEXT_TASK}:{getattr(t, 'id', t.title)}")

    ranks = {getattr(t, "prio_rank", 1) for t in open_items}
    if len(ranks) > 1:
        t = max(open_items, key=lambda t: getattr(t, "prio_rank", 1))
        return Recommendation(
            NEXT_TASK, str(t.title), PRIORITY,
            f"it's the only one you marked {getattr(t, 'priority', 'high')}",
            basis=[f"'{t.title}' is priority {getattr(t, 'priority', '')}"], candidates=titles,
            key=f"{NEXT_TASK}:{getattr(t, 'id', t.title)}")

    # 15.F3. Everything left is indistinguishable on every rule there is. Picking the first one and
    # calling it a recommendation would be presenting list order as judgement.
    return NoBasis(NEXT_TASK,
                   f"all {len(open_items)} open items look the same to me — no deadlines, no "
                   "priorities set, none attached to an objective")


# --- the log -------------------------------------------------------------------------------


def log(rec: Recommendation, path: Path | None = None) -> None:
    """Record the recommendation and what it was chosen over, so acceptance can be learned later."""
    p = path or _ledger()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**rec.row(), "outcome": ""}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def rows(path: Path | None = None) -> list[dict]:
    try:
        text = (path or _ledger()).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def outcome(key: str, taken: bool, path: Path | None = None) -> bool:
    """Record whether the owner actually did it. The column a ranker would one day train on."""
    p = path or _ledger()
    all_rows = rows(p)
    hit = False
    for r in all_rows:
        if r.get("key") == key and not r.get("outcome"):
            r["outcome"] = "taken" if taken else "ignored"
            hit = True
    if not hit:
        return False
    try:
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in all_rows) + "\n",
                     encoding="utf-8")
    except OSError:
        return False
    return True


def acceptance(path: Path | None = None) -> dict:
    """How often a recommendation was taken, and by which rule. The input to a future ranker."""
    scored = [r for r in rows(path) if r.get("outcome")]
    by_rule: dict[str, list[int]] = {}
    for r in scored:
        by_rule.setdefault(str(r.get("rule")), []).append(1 if r["outcome"] == "taken" else 0)
    return {"scored": len(scored),
            "taken": sum(1 for r in scored if r["outcome"] == "taken"),
            "by_rule": {k: {"n": len(v), "taken": sum(v)} for k, v in sorted(by_rule.items())}}


def _selfcheck() -> None:
    """ponytail: the one runnable check — a reason that cites data, and a refusal to invent one."""
    import tempfile
    from dataclasses import dataclass as _dc

    @_dc
    class T:
        id: str
        title: str
        status: str = "open"
        deadline: float | None = None
        priority: str = "normal"
        prio_rank: int = 1
        meta: dict = field(default_factory=dict)

    now = time.time()
    assert isinstance(next_task([], now=now), NoBasis)
    flat = [T("a", "one"), T("b", "two")]
    no = next_task(flat, now=now)
    assert isinstance(no, NoBasis) and "look the same" in no.why, no

    late = T("c", "water system", deadline=now - 2 * 86400)
    rec = next_task([*flat, late], now=now)
    assert rec.choice == "water system" and rec.rule == OVERDUE, rec
    assert "overdue by 2 days" in rec.reason, rec.reason
    assert rec.basis and "water system" in rec.basis[0], rec.basis
    assert len(rec.candidates) == 3, rec.candidates

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        led = Path(d) / "rec.jsonl"
        log(rec, led)
        assert rows(led)[0]["outcome"] == ""
        assert outcome(rec.key, True, led)
        assert acceptance(led)["taken"] == 1, acceptance(led)
    print("recommend self-check ok")


if __name__ == "__main__":
    _selfcheck()
