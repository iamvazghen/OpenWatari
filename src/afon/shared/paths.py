"""Where Afon's artifacts live — one spelling per location (J3.6), and one origin for state (30.F1).

A location written down twice is a location that drifts. This repo has already been bitten by it:
`protocols/auditpack.py` archived `<repo>/audit` while `brain/audit.py` wrote to
`settings.audit_log_dir`, which on this deployment is a folder in the Obsidian vault. The protocol
therefore found nothing, archived nothing, and reported "Audit archive started, sir." every single
time it ran. Nothing failed; the answer was just always empty.

**30.F1 — the two-host split, and what actually caused it.**

Afon's durable state was split across two roots that answer to different things:

  * `<repo>/` — `afon_*.sqlite`, `memory/learned`, `memory/journal`, resolved as
    `Path(__file__).parents[3]`, i.e. *wherever this code happens to be unpacked*;
  * `~/.afon/` — patterns, the relationship model, traces, voiceprint, resolved from the home
    directory, i.e. *whoever is running it*.

The first of those is the fork. `scripts/deploy_vps.sh` untars `src/afon` into the brain host's
own directory, so the repo root there is a different directory than the laptop's, and the two
hosts therefore keep two sets of learned facts under the same name. Nothing ever detected it: both
paths resolve, both stores open, and each host is confidently answering from half of what it was
told. That is not a sync bug that a sync would fix — it is a store keyed on the location of the
code, and the fix is to stop keying it there.

**The decision.** One state root per host, outside the repo, named once:
`settings.state_dir` (`AFON_STATE_DIR`), default `~/.afon`. The repo holds code and the
hand-authored overlay (`.env`, `memory/*.md` — the files the owner writes); everything Afon writes
*about* the owner lives in the state root, which a deploy never touches. Where the two hosts have
already diverged, the brain host is authoritative for machine-written state and the learned notes
are UNIONED rather than picked between — see `scripts/merge_memory.py`, which is the only half of
this that needs a human to decide anything.

`migrate_repo_state()` executes the move once, and `second_origins()` reports any legacy location
that still holds data so a half-finished migration is loud instead of silent. Moving rather than
copying is the whole point: a copy leaves both stores in place, which is the state we are ending.

This module is in `shared/` rather than `brain/` because both layers need it and `protocols/` is
not allowed to import upward (see the layering rules in bench/test_layering.py).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from afon.config import settings

REPO_ROOT = Path(__file__).resolve().parents[3]


def state_dir() -> Path:
    """The one root for everything Afon writes about the owner, on this host.

    Not created here — a getter that makes directories cannot be called from a test, a doctor
    command or a `--dry-run` without leaving a mess behind. The writers create their own.
    """
    return Path(settings.state_dir).expanduser() if settings.state_dir else Path.home() / ".afon"


def memory_dir() -> Path:
    """Learned facts (L1) and the journal (L2). Machine-written, so it lives with the state."""
    return state_dir() / "memory"


def store_path(name: str) -> Path:
    """The sqlite store called `name` — `store_path("graph")` -> `<state>/afon_graph.sqlite`.

    Replaces five separate spellings, two of which derived their directory from
    `settings.tasks_db_path`'s *parent* — so pointing the task queue at a different disk silently
    moved the coaching and presence stores with it.
    """
    return state_dir() / f"afon_{name}.sqlite"


def audit_dir() -> Path:
    """Where tool-call audit logs are written. Honours `AFON_AUDIT_LOG_DIR`."""
    return Path(settings.audit_log_dir) if settings.audit_log_dir else REPO_ROOT / "audit"


def backups_dir() -> Path:
    """Where protocols drop the artifacts `_deliver_report` looks for."""
    return REPO_ROOT / "backups"


# --- 30.F1: the one-shot move out of the repo ---------------------------------------------------

#: repo-root name -> where it belongs under the state root. These are the locations that used to
#: be resolved relative to the code, and are therefore the ones that forked per checkout.
LEGACY_STATE: dict[str, str] = {
    "afon_graph.sqlite": "afon_graph.sqlite",
    "afon_vectors.sqlite": "afon_vectors.sqlite",
    "afon_tasks.sqlite": "afon_tasks.sqlite",
    "afon_coaching.sqlite": "afon_coaching.sqlite",
    "afon_presence.sqlite": "afon_presence.sqlite",
    "afon_jobs.sqlite": "afon_jobs.sqlite",
    "afon_session.json": "afon_session.json",
    "runtime_prefs.json": "runtime_prefs.json",
    "daily_digest_state.json": "daily_digest_state.json",
    "proactive_state.json": "proactive_state.json",
    "memory/learned": "memory/learned",
    "memory/journal": "memory/journal",
}


def second_origins(repo_root: Path | None = None) -> list[Path]:
    """Legacy in-repo locations that still hold data — a second place the same memory could be.

    Empty is the invariant. A non-empty answer means this host has two candidate stores and which
    one answers depends on which code path resolved the path first, which is precisely the failure
    that made one host a week out of date on the other.
    """
    root = repo_root or REPO_ROOT
    out: list[Path] = []
    for rel in LEGACY_STATE:
        p = root / rel
        if p.is_dir():
            if any(p.iterdir()):
                out.append(p)
        elif p.is_file() and p.stat().st_size > 0:
            out.append(p)
    return out


def _is_empty_store(path: Path) -> bool:
    """True if `path` exists but holds nothing — an artifact, not a store.

    This is not fussiness. Rolling out the new default before running the migration means the brain
    opens the new location first and CREATES the destination — an empty sqlite with a schema and no
    rows — and a plain `dest.exists()` then refuses to migrate the real data behind it, forever, on
    every host that started once. Which happened here, twenty minutes after the change was written.
    """
    try:
        if path.is_dir():
            return not any(p.is_file() for p in path.rglob("*"))
        if path.stat().st_size == 0:
            return True
        if path.suffix == ".sqlite":
            import sqlite3
            con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                tables = [r[0] for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                return all(con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] == 0
                           for t in tables)
            finally:
                con.close()
        if path.suffix == ".json":
            import json
            return not json.loads(path.read_text(encoding="utf-8", errors="ignore") or "null")
    except Exception:   # noqa: BLE001 — unreadable means "do not touch it"
        return False
    return False


def migrate_repo_state(repo_root: Path | None = None, *, dry_run: bool = False) -> list[str]:
    """Move state that was living under the repo root into the state root. Idempotent.

    Never overwrites data. If both sides hold something the destination wins, the source is left
    alone, and both are REPORTED — merging two divergent stores is a decision with a wrong answer
    (the newer file is not reliably the fuller one), so it belongs in `scripts/merge_memory.py`
    where a human can see what it is about to do, not in a startup path that runs unattended.
    An EMPTY destination is replaced rather than respected — see `_is_empty_store`.
    """
    root = repo_root or REPO_ROOT
    dest_root = state_dir()
    moved: list[str] = []
    for rel, dest_rel in LEGACY_STATE.items():
        src, dest = root / rel, dest_root / dest_rel
        if not src.exists():
            continue
        if src.is_dir() and not any(src.iterdir()):
            continue                     # an empty leftover directory is not state
        if src.is_file() and src.stat().st_size == 0:
            continue
        empty_dest = dest.exists() and _is_empty_store(dest)
        if dest.exists() and not empty_dest:
            moved.append(f"KEPT BOTH  {src}  (destination {dest} already exists with data — "
                         f"merge by hand: scripts/merge_memory.py)")
            continue
        if dry_run:
            moved.append(f"would move {src} -> {dest}"
                         + (" (replacing an empty one)" if empty_dest else ""))
            continue
        if empty_dest:
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))   # not rename(): the state root can be on another volume
        moved.append(f"moved {src} -> {dest}")
    if moved and not dry_run:
        record = dest_root / "MIGRATED.txt"
        try:
            from datetime import datetime, timezone
            with record.open("a", encoding="utf-8") as fh:
                fh.write(f"# {datetime.now(timezone.utc).isoformat()}  (30.F1 single origin)\n")
                fh.write("\n".join(moved) + "\n")
        except OSError:
            pass                            # the move already happened; the note is a courtesy
    return moved


def _main() -> None:
    """python -m afon.shared.paths [--migrate]"""
    import sys

    print(f"repo    {REPO_ROOT}")
    print(f"state   {state_dir()}")
    print(f"memory  {memory_dir()}")
    if "--migrate" in sys.argv:
        for line in migrate_repo_state() or ["nothing to move"]:
            print(f"  {line}")
    else:
        extra = second_origins()
        for p in extra:
            print(f"  SECOND ORIGIN {p}  — run with --migrate")
        if not extra:
            print("  single origin: no state left under the repo root")


if __name__ == "__main__":
    _main()
