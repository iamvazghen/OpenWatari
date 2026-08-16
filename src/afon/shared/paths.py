"""Where Afon's artifacts live — one spelling per location (J3.6).

A location written down twice is a location that drifts. This repo has already been bitten by it:
`protocols/auditpack.py` archived `<repo>/audit` while `brain/audit.py` wrote to
`settings.audit_log_dir`, which on this deployment is a folder in the Obsidian vault. The protocol
therefore found nothing, archived nothing, and reported "Audit archive started, sir." every single
time it ran. Nothing failed; the answer was just always empty.

This module is in `shared/` rather than `brain/` because both layers need it and `protocols/` is
not allowed to import upward (see the layering rules in bench/test_layering.py).
"""

from __future__ import annotations

from pathlib import Path

from afon.config import settings

REPO_ROOT = Path(__file__).resolve().parents[3]


def audit_dir() -> Path:
    """Where tool-call audit logs are written. Honours `AFON_AUDIT_LOG_DIR`."""
    return Path(settings.audit_log_dir) if settings.audit_log_dir else REPO_ROOT / "audit"


def backups_dir() -> Path:
    """Where protocols drop the artifacts `_deliver_report` looks for."""
    return REPO_ROOT / "backups"
