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


def main() -> None:
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
