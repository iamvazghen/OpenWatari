"""Protocol AUDITPACK - archive the tool-call audit logs.

Launched detached with: <parent_pid> <repo_root> <python_exe>.

This protocol had never worked on this deployment. It archived ``<repo>/audit``, a directory that
does not exist here, while `brain/audit.py` writes to ``settings.audit_log_dir`` — a folder inside
the Obsidian vault. `shutil.make_archive` was never reached, `main()` returned, and the runner
still told the owner "Audit archive started, sir." Every run since the audit dir was made
configurable produced nothing, and said nothing about producing nothing.

Both halves are fixed here: the location comes from `afon.shared.paths` (one spelling), and an
empty result is written into the archive's place as a report rather than swallowed — "there was
nothing to archive" is an answer the owner can act on; silence is not.
"""

from __future__ import annotations

import sys
import zipfile
from datetime import datetime
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 3:
        return
    repo_root = Path(sys.argv[2])
    sys.path.insert(0, str(repo_root / "src"))
    from afon.shared.paths import audit_dir

    audit = audit_dir()
    # The archive goes where the RUNNER told us, like every other protocol — `_deliver_report`
    # looks in the repo root it launched us from. Only the audit source is shared, because that
    # is the location that was being spelled twice.
    backups = repo_root / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = backups / f"afon-audit-{stamp}.zip"

    logs = sorted(audit.glob("*.jsonl")) if audit.is_dir() else []
    if not logs:
        # Still produce the artifact the reporter waits for. Without it the owner is told the
        # archive started and then hears nothing at all, which is indistinguishable from a crash.
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("EMPTY.txt",
                       f"No audit logs to archive.\n\nLooked in: {audit}\n"
                       f"Directory exists: {audit.is_dir()}\n"
                       f"Generated: {datetime.now().isoformat(timespec='seconds')}\n")
        print(f"auditpack: no logs in {audit}; wrote {out.name}")
        return

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for log in logs:
            z.write(log, arcname=log.name)
    total_kb = sum(p.stat().st_size for p in logs) / 1024
    print(f"auditpack: archived {len(logs)} log(s), {total_kb:.0f} KB from {audit} -> {out.name}")


if __name__ == "__main__":
    main()
