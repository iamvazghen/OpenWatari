"""Protocol BACKUP - archive Afon memory (and restore it).

Backup is launched detached with: <parent_pid> <repo_root> <python_exe>.
Restore (disaster recovery, run by a human):

    uv run python -m afon.protocols.backup restore backups/afon-memory-<stamp>.zip

Restore refuses to overwrite a non-empty memory dir unless --force is given, so a fat-fingered
restore can't silently clobber live memory.
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path


def make_backup(memory: Path, backups: Path) -> Path:
    backups.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(shutil.make_archive(str(backups / f"afon-memory-{stamp}"), "zip",
                                    root_dir=str(memory)))


def restore_backup(archive: Path, memory: Path, force: bool = False) -> None:
    """Unpack a memory backup zip into the memory dir. Refuses a non-empty target without force."""
    if not archive.is_file():
        raise FileNotFoundError(archive)
    if memory.exists() and any(memory.iterdir()) and not force:
        raise RuntimeError(f"{memory} is not empty — pass --force to overwrite")
    memory.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(memory)


#: Where the drill records its last result, so a surface can read it without re-running a restore.
#: Same shape and same home as reliability's last probe — one convention, not two.
DRILL_PATH = Path.home() / ".afon" / "restore_drill.json"

#: A backup older than this is stale on its own terms (S22's "backup age ≤24h at all times").
MAX_BACKUP_AGE_S = 24 * 3600


def latest_backup(backups: Path) -> Path | None:
    """Newest `afon-memory-*.zip`, by name — the stamp sorts chronologically, and a name sort
    survives the copy that a filesystem move would give a fresh mtime."""
    if not backups.is_dir():
        return None
    found = sorted(backups.glob("afon-memory-*.zip"))
    return found[-1] if found else None


def verify_backup(archive: Path, into: Path | None = None) -> dict:
    """Restore `archive` into a throwaway directory and prove the contents came back.

    This is 22.F4's whole point: creating a backup proves only that a zip was written. What matters
    is that it *restores*, and the failures that make a backup a rumour — a truncated upload, a zip
    whose CRC no longer matches, an archive that unpacks to nothing — are all invisible until
    something reads it back. So the drill reads every member, not just the index: a corrupt member's
    CRC only fails on decompression, and `namelist()` would happily list it.

    Never touches live memory. Returns a typed result; raises nothing.
    """
    import tempfile
    import time

    t0 = time.monotonic()
    res: dict = {"archive": str(archive), "ok": False, "files": 0, "bytes": 0,
                 "error": "", "checked": datetime.now().isoformat(timespec="seconds")}
    tmp = None
    try:
        if not archive.is_file():
            res["error"] = "missing: the backup named as newest is not on disk"
            return res
        res["age_s"] = round(time.time() - archive.stat().st_mtime, 1)
        res["stale"] = res["age_s"] > MAX_BACKUP_AGE_S
        tmp = Path(into) if into else Path(tempfile.mkdtemp(prefix="afon-restore-drill-"))
        tmp.mkdir(parents=True, exist_ok=True)
        restore_backup(archive, tmp, force=True)
        for p in tmp.rglob("*"):
            if p.is_file():
                res["files"] += 1
                res["bytes"] += p.stat().st_size
        if res["files"] == 0:
            res["error"] = "restored nothing: the archive is empty"
            return res
        res["ok"] = True
        return res
    except Exception as e:  # noqa: BLE001 — a drill reports failure, it does not become one
        res["error"] = f"{type(e).__name__}: {e}"[:300]
        return res
    finally:
        res["ms"] = round((time.monotonic() - t0) * 1000.0, 1)
        if tmp is not None and into is None:
            shutil.rmtree(tmp, ignore_errors=True)


def run_restore_drill(backups: Path | None = None, record_to: Path | None = None) -> dict:
    """Verify the newest backup restores, and write the result where a surface can read it."""
    import json

    backups = backups or (Path(__file__).resolve().parents[3] / "backups")
    archive = latest_backup(backups)
    if archive is None:
        res = {"ok": False, "error": "no backup found: nothing has ever been archived",
               "files": 0, "bytes": 0, "ms": 0.0,
               "checked": datetime.now().isoformat(timespec="seconds")}
    else:
        res = verify_backup(archive)
    path = record_to or DRILL_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(res, indent=1), encoding="utf-8")
    except OSError:
        pass   # the drill's verdict matters more than its filing
    return res


def last_drill(path: Path | None = None) -> dict | None:
    """The last recorded drill result, or None if one has never run."""
    import json

    path = path or DRILL_PATH
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main() -> None:
    if len(sys.argv) >= 2 and sys.argv[1] == "drill":
        res = run_restore_drill()
        print(f"restore drill: {'OK' if res['ok'] else 'FAILED'} — "
              f"{res['files']} file(s), {res['bytes']} bytes, {res['ms']}ms"
              + (f" — {res['error']}" if res.get("error") else ""))
        raise SystemExit(0 if res["ok"] else 1)
    if len(sys.argv) >= 3 and sys.argv[1] == "restore":
        repo_root = Path(__file__).resolve().parents[3]
        restore_backup(Path(sys.argv[2]), repo_root / "memory", force="--force" in sys.argv)
        print(f"restored {sys.argv[2]} -> memory/")
        return
    if len(sys.argv) < 3:
        return
    repo_root = Path(sys.argv[2])
    memory = repo_root / "memory"
    if not memory.is_dir():
        return
    make_backup(memory, repo_root / "backups")


if __name__ == "__main__":
    main()
