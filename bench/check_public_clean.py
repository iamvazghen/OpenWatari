"""Public-surface cleanliness guard — no private data leaks into tracked files (Phase 5.1 / 5.5),
and every secret has exactly one home (SYSTEMS.md 36.F5).

Before OpenAfon can be public, no real secret, private host, or personal email may sit in a TRACKED
file. This greps every git-tracked file (so gitignored personal overlays — .env, real memory/*.md,
contacts.md, voiceprint — are correctly ignored) for:

  * API-key shapes (Groq gsk_…, OpenAI sk-…, GitHub ghp_…/github_pat_…)
  * private hosts (the Tailscale/VPS IP range used by this deployment)
  * the owner's personal email, EXCEPT in author-attribution files (LICENSE/NOTICE/README/SECURITY)
    where naming the author is intentional.

**One home (36.F5).** Grepping for key *shapes* only catches secrets that look like keys. It misses
the failure that was actually sitting in this repo: eight protocol passwords shipped as ordinary
English words in `config.py` — `protocol_ragnarok_password = "valhalla"`, which restarts the
laptop. No pattern would ever flag "valhalla". It leaked because a secret had *two* homes, the
environment and a fallback in tracked source, and the published one wins for every install that
never overrode it. So three structural checks run alongside the greps:

  * no credential-named setting carries a non-empty default (unset now means disabled, and
    `run_protocol` fails closed);
  * nothing outside the config layer reads a credential out of the environment — one reader, one
    home;
  * `.env` is untracked and ignored, so the personal overlay cannot become a tracked file by
    accident.

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


# --- 36.F5: secrets have exactly one home -------------------------------------------------------

#: A setting is a credential when its name ENDS in one of these. Matching a substring instead would
#: flag `llm_first_token_timeout_seconds`, and a guard that cries wolf gets an allowlist entry that
#: quietly grows until it covers the real thing.
SECRET_WORDS = {"password", "passwd", "secret", "token", "key", "credential", "credentials"}

#: The single module allowed to read a credential from the process environment.
CONFIG_MODULE = "src/afon/config.py"

#: Where a fallback secret would actually ship. `bench/` and `scripts/` are developer tooling that
#: never runs on the owner's behalf — a fixture password there is a fixture, and treating it as a
#: leak is how a guard earns the allowlist entry that later hides a real one.
SHIPPED_PREFIXES = ("src/", "deploy/")

#: Programs that legitimately read their own environment because they do not import `afon.config`.
#: `afon_ticker.py` says so in its own docstring — it is a single self-contained file deployed to
#: the VPS without the repo, so the environment IS its one home, not a second one. Anything added
#: here needs that same argument; "it was easier" is not it.
SEPARATE_PROGRAMS = {"deploy/vps/afon_ticker.py"}


def _is_secret_name(name: str) -> bool:
    return name.split("_")[-1].lower() in SECRET_WORDS


def _shipped(rel: str) -> bool:
    return rel.replace("\\", "/").startswith(SHIPPED_PREFIXES)


def secret_defaults(rel_paths: list[str]) -> list[str]:
    """Credential-named settings that carry a real value in tracked source."""
    import ast

    out: list[str] = []
    for rel in rel_paths:
        if not rel.endswith(".py") or not _shipped(rel):
            continue
        try:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        for n in ast.walk(tree):
            # `name: str = "..."` (a settings field) and `NAME = "..."` (a module constant) are the
            # two shapes a fallback secret actually takes in this codebase.
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                target, value = n.target.id, n.value
            elif isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                target, value = n.targets[0].id, n.value
            else:
                continue
            if not _is_secret_name(target) or not isinstance(value, ast.Constant):
                continue
            if value.value in (None, "", 0, False):
                continue
            out.append(f"  {rel}:{n.lineno}  [credential default]  {target} = {value.value!r}")

        # The same failure hiding one level in: `os.environ.get("X_TOKEN", "hunter2")`. A separate
        # program reading its own environment is fine; a fallback baked into that read is not, and
        # it would slip past the assignment check above because the node is a Call, not a Constant.
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call) or len(n.args) < 2:
                continue
            f = n.func
            fname = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if fname not in ("getenv", "get"):
                continue
            if fname == "get" and "environ" not in ast.unparse(f):
                continue
            var, default = n.args[0], n.args[1]
            if (isinstance(var, ast.Constant) and _is_secret_name(str(var.value))
                    and isinstance(default, ast.Constant)
                    and default.value not in (None, "", 0, False)):
                out.append(f"  {rel}:{n.lineno}  [credential default]  "
                           f"{var.value} falls back to {default.value!r}")
    return out


def stray_secret_readers(rel_paths: list[str]) -> list[str]:
    """Credential reads from the environment outside the config layer — a second home."""
    import ast

    out: list[str] = []
    for rel in rel_paths:
        norm = rel.replace("\\", "/")
        if not rel.endswith(".py") or not _shipped(rel):
            continue
        if norm == CONFIG_MODULE or norm in SEPARATE_PROGRAMS:
            continue
        try:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8", errors="ignore"))
        except (OSError, SyntaxError):
            continue
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            # os.getenv("X") / os.environ.get("X")
            fname = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if fname not in ("getenv", "get"):
                continue
            if fname == "get" and "environ" not in ast.unparse(f):
                continue
            if not n.args or not isinstance(n.args[0], ast.Constant):
                continue
            var = str(n.args[0].value)
            if _is_secret_name(var):
                out.append(f"  {rel}:{n.lineno}  [credential read outside config]  {var}")
        # os.environ["X"] as a subscript
        for n in ast.walk(tree):
            if (isinstance(n, ast.Subscript) and "environ" in ast.unparse(n.value)
                    and isinstance(n.slice, ast.Constant) and isinstance(n.slice.value, str)
                    and _is_secret_name(n.slice.value)):
                out.append(f"  {rel}:{n.lineno}  [credential read outside config]  {n.slice.value}")
    return out


def env_overlay_untracked(files: list[str]) -> list[str]:
    """`.env` must be ignored and untracked — it is the personal overlay every other check assumes."""
    out: list[str] = []
    tracked = {f.replace("\\", "/") for f in files}
    if ".env" in tracked:
        out.append("  .env is TRACKED — the personal overlay is in the repo")
    r = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT, capture_output=True)
    if r.returncode != 0:
        out.append("  .env is not gitignored — one `git add -A` away from being committed")
    return out


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

    # 36.F5 — structural, not pattern-based: these catch the secrets that look like ordinary words.
    scannable = [f for f in files if Path(f).name != self_name]
    one_home = secret_defaults(scannable) + stray_secret_readers(scannable) + env_overlay_untracked(files)

    print(f"Scanned {len(files)} tracked files for private data.")
    print(f"Checked {sum(1 for f in scannable if f.endswith('.py'))} Python files for a second "
          f"home for any secret (36.F5).\n")
    if findings or one_home:
        if findings:
            print("LEAKS FOUND — these must be scrubbed or moved to a gitignored overlay "
                  "before going public:")
            for f in findings:
                print(f)
        if one_home:
            print("SECRETS WITH MORE THAN ONE HOME (36.F5) — a fallback in tracked source is the "
                  "value every unconfigured install actually uses:")
            for f in one_home:
                print(f)
        print(f"\n=== {len(findings) + len(one_home)} leak(s) found ===")
        return 1
    print("=== clean: no secrets, private hosts, or personal email in tracked files; "
          "every credential has exactly one home ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
