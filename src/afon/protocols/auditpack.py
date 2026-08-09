"""Protocol AUDITPACK - archive local audit logs if present.

Launched detached with: <parent_pid> <repo_root> <python_exe>.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 3:
        return
    repo_root = Path(sys.argv[2])
    audit = repo_root / "audit"
    if not audit.is_dir():
        return
    backups = repo_root / "backups"
    backups.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.make_archive(str(backups / f"afon-audit-{stamp}"), "zip", root_dir=str(audit))


if __name__ == "__main__":
    main()
