"""Public-surface cleanliness guard — no private data leaks into tracked files (Phase 5.1 / 5.5).

Before OpenAfon can be public, no real secret, private host, or personal email may sit in a TRACKED
file. This greps every git-tracked file (so gitignored personal overlays — .env, real memory/*.md,
contacts.md, voiceprint — are correctly ignored) for:

  * API-key shapes (Groq gsk_…, OpenAI sk-…, GitHub ghp_…/github_pat_…)
  * private hosts (the Tailscale/VPS IP range used by this deployment)
  * the owner's personal email, EXCEPT in author-attribution files (LICENSE/NOTICE/README/SECURITY)
    where naming the author is intentional.

Exit non-zero (and list the hits) if anything leaks, so it can gate a release. Runs with no network
and no .env.

    uv run python bench/check_public_clean.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# (label, compiled pattern, set of basename allowlist where a match is intentional)
CHECKS: list[tuple[str, re.Pattern, set[str]]] = [
    ("API key (Groq/OpenAI/GitHub/Composio)",
     re.compile(r"\b(gsk_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}"
                r"|github_pat_[A-Za-z0-9_]{20,}|ak_[A-Za-z0-9]{18,})"),
     set()),
    ("private host IP (100.64.0.0/10 CGNAT/Tailscale range)",
     re.compile(r"\b100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b"),
     set()),
    ("personal email",
     re.compile(r"\biamvazghen@gmail\.com\b"),
     {"LICENSE", "NOTICE", "README.md", "SECURITY.md", "THIRD_PARTY_NOTICES.md"}),
]

# Checks applied ONLY to the product runtime surface — the installed assistant's own code and the
# shipped persona/memory TEMPLATES. Tests (bench/) and planning docs (docs/) legitimately reference the
# original owner as fixtures or project history, so they are out of scope. A stranger's installed Afon
# must never name the original owner or hardcode his timezone in what it runs or speaks.
SCOPED_CHECKS: list[tuple[str, re.Pattern, set[str]]] = [
    ("owner name in runtime/template", re.compile(r"\bVazghen\b"), set()),
    ("hardcoded personal timezone", re.compile(r"\bEurope/Berlin\b"), set()),
]


def _is_runtime_surface(rel: str) -> bool:
    """True for the shipped assistant code + persona/memory templates (not tests or docs)."""
    rel = rel.replace("\\", "/")
    if rel.startswith("src/") or rel.startswith("personality/"):
        return True
    return rel.startswith("memory/") and rel.endswith(".example.md")


# Binary / asset extensions we don't scan.
_SKIP_EXT = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".pdf", ".zip"}


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    return [f for f in out.stdout.splitlines() if f.strip()]


def main() -> int:
    files = tracked_files()
    if not files:
        print("no tracked files (not a git repo?) — skipping")
        return 0
    findings: list[str] = []
    self_name = Path(__file__).name
    for rel in files:
        if Path(rel).name == self_name:
            continue   # the guard DEFINES these patterns; never flag its own pattern strings
        if Path(rel).suffix.lower() in _SKIP_EXT:
            continue
        p = ROOT / rel
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        base = Path(rel).name
        checks = CHECKS + (SCOPED_CHECKS if _is_runtime_surface(rel) else [])
        for label, pat, allow in checks:
            if base in allow:
                continue
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                findings.append(f"  {rel}:{line}  [{label}]  …{m.group(0)[:20]}…")

    print(f"Scanned {len(files)} tracked files for private data.\n")
    if findings:
        print("LEAKS FOUND — these must be scrubbed or moved to a gitignored overlay before going public:")
        for f in findings:
            print(f)
        print(f"\n=== {len(findings)} leak(s) found ===")
        return 1
    print("=== clean: no secrets, private hosts, or personal email in tracked files ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
