"""48.F1-F3 — the shared work record: one row per unit of delegated work.

Before this there was no record. `delegate_to_fleet` sent a brief and awaited a string; the
background path wrapped that in a `TaskQueue` row which is DELETED the moment the task finishes
(`TaskQueue.drop`). So after a delegation completed, nothing anywhere said it had happened — not
what was handed over, not to whom, not whether an answer came back. "Ten real delegations tracked
end to end" (39.E1) was not a thing the system could report on, because the evidence was erased on
success.

This is the record both S39 and S48 write to. One table, because they are the same question at two
sizes: S39 posts a one-unit job and reads the answer back; S48 posts several, hands them out, and
merges. A second table for the second case would be the same columns with a different name.

What it enforces:

  * **Ownership.** A unit is claimed before it is worked, and only the holder may write its result
    slot. The claim is a conditional `UPDATE ... WHERE state='open'` — the row either moves to you
    or it does not, and `rowcount` says which. Two workers racing get one winner and one `None`.
  * **A deadline per unit**, stored, so "still running" and "late" are different answers.
  * **Merging that does not invent agreement.** Two workers given the SAME brief that come back
    with different text are a disagreement, and it is surfaced with both answers attached. Nothing
    is averaged, nothing is picked; the plan is explicit that averaging two answers produces a
    third answer nobody gave.

ponytail: sqlite's own write lock is the mutex — a single conditional UPDATE per claim, no advisory
locking and no lease renewal. That holds for workers on one host. If claims ever need to survive a
worker dying mid-unit, the upgrade is a `claimed_at` sweep that reopens stale holds, not a lock
server.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from afon.brain.dbconn import connect
from afon.shared.paths import store_path

#: A unit's life. `held` means someone owns it and is working; only they may finish it.
OPEN = "open"
HELD = "held"
DONE = "done"
FAILED = "failed"

#: How long a unit may sit unfinished before it is late. Zero means no deadline was set, and a
#: unit with no deadline is never late — which is why the delegation layer always sets one.
NO_DEADLINE = 0.0


@dataclass(frozen=True)
class Unit:
    """One piece of work: an id, an owner, a state, and a result slot."""

    id: str
    job: str
    brief: str
    seq: int = 0
    owner: str = ""
    state: str = OPEN
    created_at: float = 0.0
    claimed_at: float = 0.0
    done_at: float = 0.0
    deadline_at: float = NO_DEADLINE
    result: str = ""
    verdict: str = ""

    @property
    def open(self) -> bool:
        return self.state in (OPEN, HELD)

    def late(self, now: float | None = None) -> bool:
        """Past its deadline and still unfinished. A finished unit is never late, however slow."""
        if not self.deadline_at or not self.open:
            return False
        return (now if now is not None else time.time()) > self.deadline_at


def _path(path: str | Path | None = None) -> Path:
    return Path(path) if path else store_path("work")


def _open(path: str | Path | None = None) -> sqlite3.Connection:
    c = connect(_path(path))
    c.execute(
        "CREATE TABLE IF NOT EXISTS units ("
        "id TEXT PRIMARY KEY, job TEXT, brief TEXT, seq INTEGER, owner TEXT, state TEXT, "
        "created_at REAL, claimed_at REAL, done_at REAL, deadline_at REAL, result TEXT, verdict TEXT)"
    )
    return c


def _row(r: sqlite3.Row) -> Unit:
    return Unit(
        id=r["id"], job=r["job"], brief=r["brief"] or "", seq=int(r["seq"] or 0),
        owner=r["owner"] or "", state=r["state"] or OPEN,
        created_at=r["created_at"] or 0.0, claimed_at=r["claimed_at"] or 0.0,
        done_at=r["done_at"] or 0.0, deadline_at=r["deadline_at"] or NO_DEADLINE,
        result=r["result"] or "", verdict=r["verdict"] or "",
    )


def post(briefs: list[str] | str, *, job: str = "", deadline_s: float = NO_DEADLINE,
         path: str | Path | None = None) -> list[str]:
    """Put work on the record and return the unit ids, in the order given.

    Two units may carry the same brief on purpose — that is how a second opinion is asked for, and
    it is what makes `merge` able to detect disagreement rather than assume there is none.
    """
    if isinstance(briefs, str):
        briefs = [briefs]
    briefs = [b.strip() for b in briefs if b and b.strip()]
    if not briefs:
        return []
    job = job or uuid.uuid4().hex[:8]
    now = time.time()
    due = now + deadline_s if deadline_s > 0 else NO_DEADLINE
    ids = [f"{job}#{i}" for i in range(len(briefs))]
    try:
        with _open(path) as c:
            c.executemany(
                "INSERT OR REPLACE INTO units "
                "(id, job, brief, seq, owner, state, created_at, claimed_at, done_at, deadline_at, "
                "result, verdict) VALUES (?,?,?,?,'',?,?,0,0,?,'','')",
                [(uid, job, b, i, OPEN, now, due) for i, (uid, b) in enumerate(zip(ids, briefs))],
            )
    except sqlite3.Error:
        return []
    return ids


def claim(job: str, worker: str, *, path: str | Path | None = None) -> Unit | None:
    """Take the next open unit of `job` for `worker`, or None if there is nothing left to take.

    The conditional UPDATE is the whole lock: whoever's statement lands first flips the row out of
    `open` and the loser's `rowcount` is zero, so it moves on to the next candidate. No unit is
    ever handed to two workers.
    """
    try:
        with _open(path) as c:
            while True:
                row = c.execute(
                    "SELECT id FROM units WHERE job=? AND state=? ORDER BY seq LIMIT 1",
                    (job, OPEN),
                ).fetchone()
                if row is None:
                    return None
                cur = c.execute(
                    "UPDATE units SET owner=?, state=?, claimed_at=? WHERE id=? AND state=?",
                    (worker, HELD, time.time(), row["id"], OPEN),
                )
                if cur.rowcount:
                    got = c.execute("SELECT * FROM units WHERE id=?", (row["id"],)).fetchone()
                    return _row(got) if got else None
                # Someone else took it between the select and the update. Try the next one.
    except sqlite3.Error:
        return None


def finish(unit_id: str, worker: str, result: str, *, verdict: str = "",
           path: str | Path | None = None) -> bool:
    """Write the result slot. Only the holder may; False means the caller does not own this unit."""
    try:
        with _open(path) as c:
            cur = c.execute(
                "UPDATE units SET state=?, result=?, verdict=?, done_at=? "
                "WHERE id=? AND owner=? AND state=?",
                (DONE, (result or "").strip(), verdict, time.time(), unit_id, worker, HELD),
            )
            return bool(cur.rowcount)
    except sqlite3.Error:
        return False


def fail(unit_id: str, worker: str, error: str, *, path: str | Path | None = None) -> bool:
    """Record that this unit's worker could not do it. A failed unit is not a missing unit."""
    try:
        with _open(path) as c:
            cur = c.execute(
                "UPDATE units SET state=?, result=?, done_at=? WHERE id=? AND owner=? AND state=?",
                (FAILED, (error or "").strip()[:500], time.time(), unit_id, worker, HELD),
            )
            return bool(cur.rowcount)
    except sqlite3.Error:
        return False


def get(unit_id: str, *, path: str | Path | None = None) -> Unit | None:
    try:
        with _open(path) as c:
            r = c.execute("SELECT * FROM units WHERE id=?", (unit_id,)).fetchone()
            return _row(r) if r else None
    except sqlite3.Error:
        return None


def units(job: str, *, path: str | Path | None = None) -> list[Unit]:
    try:
        with _open(path) as c:
            return [_row(r) for r in c.execute(
                "SELECT * FROM units WHERE job=? ORDER BY seq", (job,))]
    except sqlite3.Error:
        return []


def recent(limit: int = 20, *, path: str | Path | None = None) -> list[Unit]:
    """The last `limit` units posted, newest first — the delegation log 39.E1 wants to review."""
    try:
        with _open(path) as c:
            return [_row(r) for r in c.execute(
                "SELECT * FROM units ORDER BY created_at DESC, seq DESC LIMIT ?", (int(limit),))]
    except sqlite3.Error:
        return []


def unfinished(*, path: str | Path | None = None) -> list[Unit]:
    """Every unit still open or held, oldest first — what is outstanding across all jobs."""
    try:
        with _open(path) as c:
            return [_row(r) for r in c.execute(
                "SELECT * FROM units WHERE state IN (?,?) ORDER BY created_at, seq", (OPEN, HELD))]
    except sqlite3.Error:
        return []


# --- merging ------------------------------------------------------------------------------------


@dataclass
class Merged:
    """What a job amounts to, with the disagreements left in."""

    job: str
    parts: list[tuple[str, str]] = field(default_factory=list)          # (brief, result)
    conflicts: list[tuple[str, list[str]]] = field(default_factory=list)  # (brief, answers)
    missing: list[str] = field(default_factory=list)                    # briefs with no answer yet
    failed: list[tuple[str, str]] = field(default_factory=list)         # (brief, error)

    @property
    def agreed(self) -> bool:
        return not self.conflicts

    @property
    def complete(self) -> bool:
        return not self.missing and not self.failed

    def report(self) -> str:
        """One text, deterministic, that never hides a disagreement inside a summary."""
        out: list[str] = []
        for brief, result in self.parts:
            out.append(f"{brief}: {result}")
        for brief, answers in self.conflicts:
            joined = "\n".join(f"  - {a}" for a in answers)
            out.append(f"{brief}: THE WORKERS DISAGREE — {len(answers)} different answers:\n{joined}")
        for brief, error in self.failed:
            out.append(f"{brief}: no answer — {error}")
        for brief in self.missing:
            out.append(f"{brief}: still outstanding")
        return "\n\n".join(out)

    def spoken(self) -> str:
        """The one line the owner hears about the state of the job."""
        bits = []
        if self.parts:
            bits.append(f"{len(self.parts)} part{'s' if len(self.parts) != 1 else ''} came back")
        if self.conflicts:
            bits.append(f"{len(self.conflicts)} where they disagree — I've left both answers in "
                        "rather than split the difference")
        if self.failed:
            bits.append(f"{len(self.failed)} failed")
        if self.missing:
            bits.append(f"{len(self.missing)} still outstanding")
        return ("On that job, sir: " + "; ".join(bits) + ".") if bits else "Nothing on that job yet, sir."


def merge(job: str, *, path: str | Path | None = None) -> Merged:
    """Fold a job's units into one result, deterministically.

    Units are grouped by their brief, because two units with the same brief were asked the same
    question and are therefore comparable; two units with different briefs are different pieces of
    one job and simply follow one another in the order they were posted.

    Where a brief has more than one distinct answer, BOTH are kept and the brief is listed as a
    conflict. It is never resolved here — picking the longer one, the newer one, or the mean of two
    numbers all produce an answer no worker actually gave, and the owner would have no way to tell.
    """
    rows = units(job, path=path)
    out = Merged(job=job)
    seen: list[str] = []
    by_brief: dict[str, list[Unit]] = {}
    for u in rows:
        if u.brief not in by_brief:
            by_brief[u.brief] = []
            seen.append(u.brief)
        by_brief[u.brief].append(u)

    for brief in seen:                                   # posting order — the deterministic part
        group = by_brief[brief]
        answers: list[str] = []
        for u in sorted(group, key=lambda u: u.seq):
            if u.state == DONE and u.result and u.result not in answers:
                answers.append(u.result)
        if len(answers) > 1:
            out.conflicts.append((brief, answers))
            continue
        if answers:
            out.parts.append((brief, answers[0]))
            continue
        bad = next((u for u in group if u.state == FAILED), None)
        if bad is not None:
            out.failed.append((brief, bad.result or "the worker failed"))
        else:
            out.missing.append(brief)
    return out


def _selfcheck() -> None:
    """ponytail: the one runnable check — a double claim and a disagreement, which are the two
    things this module exists to make impossible and impossible-to-hide respectively."""
    import tempfile

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        p = Path(d) / "work.sqlite"
        ids = post(["find the price", "find the price", "write it up"], job="j", deadline_s=60,
                   path=p)
        assert len(ids) == 3, ids
        a = claim("j", "worker-a", path=p)
        b = claim("j", "worker-b", path=p)
        assert a and b and a.id != b.id, (a, b)
        assert finish(a.id, "worker-a", "nineteen euros", path=p)
        assert not finish(a.id, "worker-b", "hijacked", path=p), "a non-holder wrote the result slot"
        assert finish(b.id, "worker-b", "twenty-one euros", path=p)
        m = merge("j", path=p)
        assert not m.agreed and m.conflicts[0][1] == ["nineteen euros", "twenty-one euros"], m
        assert m.missing == ["write it up"], m
        assert "DISAGREE" in m.report(), m.report()
    print("coordination self-check ok")


if __name__ == "__main__":
    _selfcheck()
