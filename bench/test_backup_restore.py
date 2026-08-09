"""Restore drill — a backup that has never been restored is not a backup.

Hermetic: temp memory dir -> backup zip -> destroy -> restore -> byte-identical. Also proves the
no-clobber guard (restore into non-empty memory requires force).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from afon.protocols.backup import make_backup, restore_backup


def test_backup_restore_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        memory = root / "memory"
        (memory / "learned").mkdir(parents=True)
        (memory / "learned" / "fact.md").write_text("owner runs a rabbit farm", encoding="utf-8")
        (memory / "journal.md").write_text("day one", encoding="utf-8")

        archive = make_backup(memory, root / "backups")
        assert archive.is_file() and archive.stat().st_size > 0

        # disaster: memory is wiped
        import shutil

        shutil.rmtree(memory)
        restore_backup(archive, memory)
        assert (memory / "learned" / "fact.md").read_text(encoding="utf-8") == "owner runs a rabbit farm"
        assert (memory / "journal.md").read_text(encoding="utf-8") == "day one"

        # guard: restoring over live memory must refuse without force…
        with pytest.raises(RuntimeError):
            restore_backup(archive, memory)
        # …and proceed with it.
        restore_backup(archive, memory, force=True)


# Runs as a script (see the note in test_pc_agent_refuse.py) — without this the roundtrip and the
# no-clobber guard never executed, and the suite still went green.
if __name__ == "__main__":
    test_backup_restore_roundtrip()
    print("=== 5/5 checks passed ===")
