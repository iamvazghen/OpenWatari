"""Memory hygiene (P1 #6) — keep the learned/journal stores healthy for 24/7 operation.

A companion accumulates state every day: near-duplicate learned facts pile up (``remember`` only
dedups *exact* matches), the active set grows without bound, and journal files accrue one per day
forever — slowly re-spending the prompt budget we reclaimed and dulling recall. This module is the
janitor. It is dependency-free (token-set similarity, no embeddings) and only ever moves/deletes
files inside ``memory/learned`` and ``memory/journal``.

Three jobs, all idempotent:
  * ``compact_learned``  — drop near-identical facts, keeping the newest representative.
  * ``cap_learned``      — archive the oldest facts beyond a soft cap (bounds the active set).
  * ``rotate_journals``  — move journal days older than N days into ``journal/archive/``.

``run_maintenance`` runs all three and is the importable daily job target;
``schedule_maintenance`` registers it on the brain scheduler.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loguru import logger

from afon.brain.loops import ticks
from afon.brain.memory import STORE, MemoryStore, _terms


def _tokset(text: str) -> frozenset[str]:
    return frozenset(_terms(text))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def compact_learned(store: MemoryStore | None = None, threshold: float = 0.82) -> dict:
    """Delete near-duplicate learned facts (token-set Jaccard >= threshold), keep the newest.

    Returns ``{"removed": n, "kept": m}``. Newest-first so the surviving copy is the most recent
    phrasing. Exact dupes are already prevented at write time; this catches paraphrases.
    """
    store = store or STORE
    notes = sorted(store._iter_notes(), key=lambda n: n.path.stat().st_mtime, reverse=True)
    kept_sets: list[frozenset[str]] = []
    removed = 0
    for note in notes:
        ts = _tokset(note.text)
        if ts and any(_jaccard(ts, k) >= threshold for k in kept_sets):
            try:
                note.path.unlink()
                removed += 1
            except OSError:
                pass
        else:
            kept_sets.append(ts)
    if removed:
        logger.info(f"memory hygiene: removed {removed} near-duplicate fact(s), {len(kept_sets)} kept")
    return {"removed": removed, "kept": len(kept_sets)}


def cap_learned(store: MemoryStore | None = None, max_facts: int = 500) -> dict:
    """Archive the oldest learned facts beyond ``max_facts`` so the active set stays bounded.

    Archived notes move to ``learned/archive/`` — out of the recall/digest hot path (which globs
    ``learned/*.md`` non-recursively) but never lost. Returns ``{"archived": n}``.
    """
    store = store or STORE
    notes = sorted(store._iter_notes(), key=lambda n: n.path.stat().st_mtime, reverse=True)
    if len(notes) <= max_facts:
        return {"archived": 0}
    archive = store.learned_dir / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    archived = 0
    for note in notes[max_facts:]:
        try:
            note.path.rename(archive / note.path.name)
            archived += 1
        except OSError:
            pass
    if archived:
        logger.info(f"memory hygiene: archived {archived} old fact(s) beyond cap {max_facts}")
    return {"archived": archived}


def rotate_journals(store: MemoryStore | None = None, keep_days: int = 35) -> dict:
    """Move journal day-files older than ``keep_days`` into ``journal/archive/``.

    Keeps ``read_journal`` (which globs ``journal/*.md``) fast and recent without losing history.
    Returns ``{"archived": n}``.
    """
    store = store or STORE
    if not store.journal_dir.is_dir():
        return {"archived": 0}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).date()
    archive = store.journal_dir / "archive"
    archived = 0
    for p in store.journal_dir.glob("*.md"):
        try:
            day = datetime.strptime(p.stem, "%Y-%m-%d").date()
        except ValueError:
            continue  # not a dated journal file
        if day < cutoff:
            archive.mkdir(parents=True, exist_ok=True)
            try:
                p.rename(archive / p.name)
                archived += 1
            except OSError:
                pass
    if archived:
        logger.info(f"memory hygiene: archived {archived} old journal day(s)")
    return {"archived": archived}


@ticks("memory-maintenance")
async def run_maintenance() -> str:
    """Daily job target (importable for the scheduler's jobstore). Runs all hygiene passes."""
    c = compact_learned()
    cap = cap_learned()
    r = rotate_journals()
    msg = (f"memory hygiene: deduped {c['removed']} fact(s) ({c['kept']} active), "
           f"archived {cap['archived']} over-cap fact(s) + {r['archived']} journal day(s)")
    logger.info(msg)
    return msg


def schedule_maintenance(scheduler, daily_hhmm: str = "04:00") -> None:
    """Register ``run_maintenance`` as a daily cron job on the brain scheduler (idempotent)."""
    from apscheduler.triggers.cron import CronTrigger

    from afon.brain.scheduler import USER_TZ

    sched = scheduler._ensure()
    hh, mm = (int(x) for x in daily_hhmm.split(":"))
    sched.add_job(
        run_maintenance,
        trigger=CronTrigger(hour=hh, minute=mm, timezone=USER_TZ),
        id="memory-maintenance",
        name="memory hygiene",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )
    logger.info(f"memory hygiene scheduled daily at {daily_hhmm}")
