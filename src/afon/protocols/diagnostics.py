"""Protocol DIAGNOSTICS - write a local configuration health report.

Launched detached with: <parent_pid> <repo_root> <python_exe>.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 4:
        return
    repo_root = Path(sys.argv[2])
    python_exe = sys.argv[3]
    backups = repo_root / "backups"
    backups.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report = backups / f"afon-diagnostics-{stamp}.txt"

    proc = subprocess.run(
        [python_exe, "bench/check_config.py"],
        cwd=str(repo_root),
        text=True,
        capture_output=True,
        timeout=60,
    )
    body = [
        f"Afon diagnostics {stamp}",
        f"exit_code={proc.returncode}",
        "",
        "STDOUT",
        proc.stdout,
        "",
        "STDERR",
        proc.stderr,
    ]
    report.write_text("\n".join(body), encoding="utf-8")


if __name__ == "__main__":
    main()
