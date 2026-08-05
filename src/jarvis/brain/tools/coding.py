"""Coding & self-improvement tools (Phase 13) — Jarvis can read, edit, test, and *safely* commit.

This is what lets Jarvis improve his own codebase over time, with a hard safety rail: **every change
is reversible through git, and nothing destructive is reachable.** Concretely:

* **Repo-scoped file I/O** — `read_source` / `write_source` / `list_source` operate only inside the
  repo, and refuse secrets (`.env`, `*.session`, `voiceprint.json`, `audit/`, `backups/`, `.git/`).
* **Verify before trusting** — `run_tests` runs the full suite, `lint` runs ruff. The
  self-improvement skill tells him to test before he commits.
* **Reversible git only** — `git_status` / `git_diff` / `git_log` (read) and `git_new_branch` /
  `git_commit` / `git_push` / `git_revert` (write). The write set is deliberately limited to
  *additive* history: a revert is a new commit, never a rewrite. There is **no** reset, force-push,
  rebase, clean, or branch-delete tool, so he can't lose your work.

Commits, pushes, and source writes are confirm-gated (they're in `proactive.CONFIRM_TIER`), so the
persona reads back what it's about to do and waits for a yes.

**Where this runs matters more than anything above.** ``_REPO`` is derived from this file's own
location, so on the VPS brain it resolves to the *deploy tree* — which is a tar-extracted copy that
happens to carry a ``.git`` pointing at the real GitHub remote, permanently dirty because every
deploy untars over it. Left alone, that made ``git_commit``/``git_push`` able to publish deploy
state on top of a stale commit, and made every ``write_source`` edit vanish at the next deploy.
So all four primitives (exec, read, write, list) now go through ``_dispatch`` to the laptop when a
PC_LINK is connected — the same routing ``browser``, ``camera`` and ``screenshot`` already use —
and act on the owner's real checkout. With no laptop connected they fall back to running locally,
which is correct when the brain itself IS the laptop. The laptop side re-validates every path and
every command; the brain's checks are convenience, the executor's are authority.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from jarvis.brain.tools.base import clip, tool_error
from jarvis.config import settings

_REPO = Path(__file__).resolve().parents[4]

# Top-level dirs/files Jarvis may never read or write (secrets, runtime state, vcs internals).
_BLOCKED_TOP = {".git", ".venv", ".jarvis-browser", "audit", "backups", "node_modules"}
_BLOCKED_NAMES = {"voiceprint.json", "jarvis_jobs.sqlite"}
_BLOCKED_SUFFIX = {".session", ".session-journal"}

# Git subcommands Jarvis is allowed to run. Anything that rewrites or discards history is absent
# by design (no reset/rebase/clean/checkout-of-files/push --force/branch -D).
_GIT_READ = {"status", "diff", "log", "branch", "show", "rev-parse"}


def _is_secret(p: Path) -> bool:
    name = p.name
    return (
        name == ".env"
        or name.startswith(".env")
        or name in _BLOCKED_NAMES
        or p.suffix in _BLOCKED_SUFFIX
    )


def _safe_path(rel: str, root: Path | None = None) -> Path | None:
    """Resolve a repo-relative path, or None if it escapes the repo or hits a blocked target.

    ``root`` defaults to this host's repo. The laptop executor passes its OWN root, so the same
    rule is enforced where the file actually lives."""
    root = root or _REPO
    rel = (rel or "").strip().strip("/\\")
    if not rel:
        return None
    try:
        p = (root / rel).resolve()
    except Exception:  # noqa: BLE001
        return None
    if p != root and root not in p.parents:
        return None  # path traversal outside the repo
    parts = p.relative_to(root).parts
    if parts and parts[0] in _BLOCKED_TOP:
        return None
    if _is_secret(p):
        return None
    return p


def _safe_rel(rel: str) -> str | None:
    """Validate a repo-relative path WITHOUT touching the filesystem, and return it normalised.

    Absolute paths must never cross the link: a path resolved on the VPS ('/home/openclaw/jarvis/…')
    is meaningless on the laptop, and git would either miss the file or stage the wrong one. Every
    path that travels to the executor — or into a git argument — goes through here first, so it stays
    repo-relative and is re-resolved against whichever repo actually runs the command."""
    rel = (rel or "").strip().replace("\\", "/")
    # Refuse an absolute path outright rather than stripping it to something relative: '/etc/passwd'
    # quietly becoming '<repo>/etc/passwd' is a wrong answer dressed as a working one.
    if not rel or rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        return None
    rel = rel.strip("/")
    parts = [seg for seg in rel.split("/") if seg not in ("", ".")]
    if not parts or ".." in parts:
        return None
    if parts[0] in _BLOCKED_TOP:
        return None
    name = parts[-1]
    if (name == ".env" or name.startswith(".env") or name in _BLOCKED_NAMES
            or any(name.endswith(sfx) for sfx in _BLOCKED_SUFFIX)):
        return None
    return "/".join(parts)


def _enabled() -> str | None:
    if not settings.coding_tools_enabled:
        return "My coding tools are switched off right now, sir (JARVIS_CODING_TOOLS_ENABLED)."
    return None


# --- laptop-side executor (these run inside pc_agent, where the real repo is) -------------
#
# The brain already refuses history-rewriting git and non-repo paths, but a forwarded op must never
# depend on the caller being well-behaved: this is an allow-list, so a command that is not explicitly
# permitted is refused rather than merely "not blocked". `git reset/rebase/clean`, force-push and
# branch deletion have no entry here and therefore cannot be run at all, whatever the brain sends.
_EXEC_ALLOW = {"git", "uv"}
_GIT_ALLOWED_SUB = {"status", "diff", "log", "branch", "show", "rev-parse", "add", "commit",
                    "push", "revert", "checkout", "remote"}
_GIT_FORBIDDEN = ("--force", "--force-with-lease", "--hard", "--soft", "--mixed", "-D", "-f")


def _exec_refusal(argv: list[str]) -> str | None:
    """Why this command may not run on the laptop, or None if it may. Allow-list, not deny-list."""
    if not argv:
        return "empty command"
    exe = Path(argv[0]).name.lower().removesuffix(".exe")
    if exe not in _EXEC_ALLOW:
        return f"'{argv[0]}' is not a permitted executable"
    rest = argv[1:]
    if exe == "git":
        # `git -c user.name=… commit` is how authored commits are made; skip the -c pairs to find
        # the real subcommand rather than refusing the whole form.
        i = 0
        while i < len(rest) and rest[i] == "-c":
            i += 2
        sub = rest[i] if i < len(rest) else ""
        if sub not in _GIT_ALLOWED_SUB:
            return f"git '{sub or '(none)'}' is not a permitted subcommand"
        if any(tok in _GIT_FORBIDDEN for tok in rest):
            return "that git command carries a history-rewriting or force flag"
        # checkout is permitted ONLY to create a branch: bare `git checkout <path>` discards the
        # owner's uncommitted work, which is exactly the irreversibility this module forbids.
        if sub == "checkout" and "-b" not in rest:
            return "git checkout is only permitted with -b (new branch)"
        return None
    # uv: exactly the two verification commands this module issues.
    if rest[:2] == ["run", "ruff"]:
        return None
    if rest[:3] == ["run", "python", "bench/run_all_tests.py"]:
        return None
    return "only 'uv run ruff …' and the test runner are permitted"


async def _exec_local(argv: list[str], timeout: int, root: Path) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *argv, cwd=str(root),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, f"timed out after {timeout}s"
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace")


async def _pc_repo_exec(args: dict) -> str:
    """PC op: run one allow-listed command in the laptop's repo. Returns JSON {rc, out}."""
    argv = [str(a) for a in (args.get("argv") or [])]
    try:
        timeout = max(1, min(int(args.get("timeout") or 240), 900))
    except (TypeError, ValueError):
        timeout = 240
    refusal = _exec_refusal(argv)
    if refusal:
        return json.dumps({"rc": 126, "out": f"refused on the laptop: {refusal}"})
    rc, out = await _exec_local(argv, timeout, _REPO)
    return json.dumps({"rc": rc, "out": out})


async def _pc_repo_read(args: dict) -> str:
    p = _safe_path(_safe_rel(args.get("path", "")) or "", _REPO)
    if p is None or not p.is_file():
        return json.dumps({"ok": False, "out": "no such readable file in the repo"})
    return json.dumps({"ok": True, "out": p.read_text(encoding="utf-8", errors="replace")})


async def _pc_repo_write(args: dict) -> str:
    p = _safe_path(_safe_rel(args.get("path", "")) or "", _REPO)
    if p is None:
        return json.dumps({"ok": False, "out": "path is outside the repo or protected"})
    content = args.get("content")
    if content is None:
        return json.dumps({"ok": False, "out": "no content given"})
    p.parent.mkdir(parents=True, exist_ok=True)
    existed = p.is_file()
    p.write_text(str(content), encoding="utf-8")
    return json.dumps({"ok": True, "existed": existed, "out": str(len(str(content)))})


async def _pc_repo_list(args: dict) -> str:
    p = _safe_path(_safe_rel(args.get("path") or ".") or ".", _REPO)
    if p is None or not p.is_dir():
        return json.dumps({"ok": False, "out": "not a listable folder in the repo"})
    entries = sorted((("📁 " if c.is_dir() else "") + c.name) for c in p.iterdir()
                     if c.name not in _BLOCKED_TOP and not _is_secret(c))
    return json.dumps({"ok": True, "out": entries[:80]})


# Merged into the pc_agent executor (like system/camera/browser), so these run on the laptop.
LOCAL_HANDLERS = {
    "repo_exec": _pc_repo_exec,
    "repo_read": _pc_repo_read,
    "repo_write": _pc_repo_write,
    "repo_list": _pc_repo_list,
}


# --- brain-side primitives (route to the laptop when one is connected) --------------------

async def _forward(op: str, args: dict, timeout: float) -> dict:
    """Run a repo op where the repo is, and decode its JSON envelope.

    A transport failure is returned as a normal failed result rather than raised: every caller
    already renders a spoken failure, and an exception here would surface as a bare TypeError.

    **No silent local fallback.** `_dispatch` normally degrades to running the op on this host when
    no laptop is linked, which is right for a camera (it simply fails) but wrong here: on the VPS the
    local repo is the deploy tree whose git points at the real remote, so falling back would restore
    the exact hazard this routing exists to remove — and it would do it invisibly, at the moment the
    owner is least able to notice. Every supported deployment runs pc_agent, so an inactive link
    means something is down; say so and run nothing, exactly as the machine-level protocols do."""
    from jarvis.brain.pc_link import PC_LINK
    from jarvis.brain.tools.system import _dispatch

    if not PC_LINK.active:
        return {"ok": False, "rc": 1,
                "out": "your laptop isn't connected, so I can't touch the repo — it lives there, and "
                       "the copy on this server is a deploy artefact I must not commit from"}
    local = LOCAL_HANDLERS[op]
    try:
        raw = await _dispatch(op, args, local, timeout=timeout)
    except Exception as e:  # noqa: BLE001 — laptop dropped mid-op
        return {"ok": False, "rc": 1, "out": f"the laptop didn't answer ({type(e).__name__})"}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        # _dispatch's own "your laptop didn't respond" note is plain prose, not JSON.
        return {"ok": False, "rc": 1, "out": str(raw)}


async def _run(cmd: list[str], timeout: int = 240) -> tuple[int, str]:
    """Run a subprocess in the repo (on the laptop when linked), return (rc, combined output)."""
    # The transport wait must outlast the command itself, or a passing 7-minute test run comes back
    # as a timeout while it is still succeeding on the laptop.
    d = await _forward("repo_exec", {"argv": list(cmd), "timeout": int(timeout)}, timeout + 30)
    return int(d.get("rc", 1) or 0), str(d.get("out", ""))


async def _git(*args: str, timeout: int = 60) -> tuple[int, str]:
    return await _run(["git", *args], timeout=timeout)


# ---- file I/O ---------------------------------------------------------------------------

async def read_source(args: dict) -> str:
    if (off := _enabled()):
        return off
    rel = _safe_rel(args.get("path", ""))
    if rel is None:
        return "I can't read that path, sir — it's outside the project or a protected file."
    try:
        d = await _forward("repo_read", {"path": rel}, 60)
        if not d.get("ok"):
            return f"There's no file at {rel} I can read, sir ({d.get('out', '')})."
        text = str(d.get("out", ""))
        return f"{rel} ({len(text)} chars):\n{clip(text, 6000)}"
    except Exception as e:  # noqa: BLE001
        return tool_error("read source", e)


async def write_source(args: dict) -> str:
    if (off := _enabled()):
        return off
    rel = _safe_rel(args.get("path", ""))
    if rel is None:
        return "I won't write there, sir — it's outside the project or a protected file."
    content = args.get("content")
    if content is None:
        return "What should I write into that file, sir?"
    try:
        d = await _forward("repo_write", {"path": rel, "content": content}, 60)
        if not d.get("ok"):
            return f"I couldn't write {rel}, sir — {d.get('out', 'the write was refused')}."
        verb = "Updated" if d.get("existed") else "Created"
        return (f"{verb} {rel}, sir ({len(str(content))} chars). "
                "It's uncommitted — run the tests, then commit so it's reversible.")
    except Exception as e:  # noqa: BLE001
        return tool_error("write source", e)


async def list_source(args: dict) -> str:
    if (off := _enabled()):
        return off
    rel = _safe_rel(args.get("path") or ".") or "."
    try:
        d = await _forward("repo_list", {"path": rel}, 60)
        if not d.get("ok"):
            return "That's not a folder I can list, sir."
        entries = d.get("out") or []
        return f"{rel}: " + ", ".join(str(e) for e in entries)
    except Exception as e:  # noqa: BLE001
        return tool_error("list source", e)


# ---- verify -----------------------------------------------------------------------------

async def run_tests(args: dict) -> str:
    if (off := _enabled()):
        return off
    rc, out = await _run(
        ["uv", "run", "python", "bench/run_all_tests.py"], timeout=420
    )
    tail = "\n".join(line for line in out.splitlines() if "passed," in line or "PASS" in line or "FAIL" in line)
    verdict = "all green" if rc == 0 else "FAILURES — do not commit yet"
    return f"Test run {verdict}, sir.\n{clip(tail or out, 1500)}"


async def lint(args: dict) -> str:
    if (off := _enabled()):
        return off
    target = (args.get("path") or "src bench").strip()
    targets = [t for t in (_safe_rel(seg) for seg in target.split()) if t]
    if not targets:
        return "I can't lint that path, sir."
    rc, out = await _run(["uv", "run", "ruff", "check", *targets], timeout=120)
    return ("Lint clean, sir." if rc == 0 else f"Ruff found issues, sir:\n{clip(out, 1500)}")


# ---- git (reversible only) --------------------------------------------------------------

async def git_status(args: dict) -> str:
    if (off := _enabled()):
        return off
    rc, out = await _git("status", "--short", "--branch")
    return clip(out or "clean working tree", 1500) if rc == 0 else tool_error("git status", Exception(out))


async def git_diff(args: dict) -> str:
    if (off := _enabled()):
        return off
    rel = _safe_rel(args.get("path") or "")
    cmd = ["diff"] + ([rel] if rel else [])
    rc, out = await _git(*cmd)
    return clip(out or "no unstaged changes", 4000)


async def git_log(args: dict) -> str:
    if (off := _enabled()):
        return off
    try:
        n = max(1, min(int(args.get("n") or 10), 50))
    except (TypeError, ValueError):
        n = 10
    rc, out = await _git("log", f"-{n}", "--oneline")
    return clip(out or "no commits yet", 1500)


async def git_new_branch(args: dict) -> str:
    if (off := _enabled()):
        return off
    name = (args.get("name") or "").strip().replace(" ", "-")
    if not name:
        return "What should I name the branch, sir?"
    rc, out = await _git("checkout", "-b", name)
    return f"On a new branch '{name}', sir." if rc == 0 else f"Couldn't branch, sir: {clip(out, 200)}"


async def git_commit(args: dict) -> str:
    if (off := _enabled()):
        return off
    message = (args.get("message") or "").strip()
    if not message:
        return "What should the commit message say, sir?"
    paths = args.get("paths")
    # Stage either the named paths (validated) or all tracked changes.
    if isinstance(paths, list) and paths:
        # Repo-RELATIVE, never resolved: an absolute path built on the brain host means nothing on
        # the laptop where the commit actually happens.
        safe = [r for r in (_safe_rel(p) for p in paths) if r]
        if not safe:
            return "None of those paths are ones I'm allowed to commit, sir."
        await _git("add", *safe)
    else:
        await _git("add", "-A")
    full = f"{message}\n\nMade by Jarvis (self-improvement)."
    rc, out = await _git(
        "-c", f"user.name={settings.git_author_name}",
        "-c", f"user.email={settings.git_author_email}",
        "commit", "-m", full,
    )
    if rc == 0:
        _, head = await _git("rev-parse", "--short", "HEAD")
        return f"Committed as {head.strip()}, sir — fully reversible. {clip(out.splitlines()[-1] if out else '', 120)}"
    return f"Nothing to commit or commit failed, sir: {clip(out, 200)}"


async def git_push(args: dict) -> str:
    if (off := _enabled()):
        return off
    rc, branch = await _git("rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0:
        # Don't let a transport/permission failure be reported as "no origin remote" — that sent the
        # owner off to fix his GitHub config when the real cause was an offline laptop.
        return f"I couldn't reach the repo to push, sir — {clip(branch, 160)}."
    branch = branch.strip() or "HEAD"
    rc, remotes = await _git("remote")
    if rc != 0:
        return f"I couldn't reach the repo to push, sir — {clip(remotes, 160)}."
    if "origin" not in (remotes or ""):
        return ("There's no 'origin' remote yet, sir — add your GitHub repo first, then I can push.")
    rc, out = await _git("push", "origin", branch, timeout=120)
    return (f"Pushed '{branch}' to GitHub, sir." if rc == 0
            else f"Push didn't go through, sir: {clip(out, 200)}")


async def git_revert(args: dict) -> str:
    if (off := _enabled()):
        return off
    commit = (args.get("commit") or "HEAD").strip()
    # --no-edit keeps it non-interactive; a revert is a NEW commit, so nothing is lost.
    rc, out = await _git(
        "-c", f"user.name={settings.git_author_name}",
        "-c", f"user.email={settings.git_author_email}",
        "revert", "--no-edit", commit,
    )
    return (f"Reverted {commit} with a new commit, sir — the old state is restored and still in history."
            if rc == 0 else f"Couldn't revert {commit}, sir: {clip(out, 200)}")


async def create_github_issue(args: dict) -> str:
    """File a GitHub issue in ONE call via the configured PAT — the common dev action by voice.

    First-class shortcut over the 2-step Composio router (find_tools -> run_tool): "open me an issue
    titled X" becomes a single tool call. Confirm-gated (it's outward-facing). Falls back to a spoken
    'not configured' note if no PAT/repo is set. Repo defaults to JARVIS_GITHUB_REPO; an explicit
    'owner/repo' argument overrides it.
    """
    token = settings.github_token
    repo = (args.get("repo") or settings.github_repo or "").strip()
    if not token or not repo:
        from jarvis.brain.tools.base import not_configured
        return not_configured(
            "GitHub issues",
            "a GitHub PAT (JARVIS_GITHUB_TOKEN, Issues: read/write) and JARVIS_GITHUB_REPO (owner/repo)")
    title = (args.get("title") or "").strip()
    if not title:
        return "What should the issue title be, sir?"
    body = (args.get("body") or "").strip()
    labels = args.get("labels") or []
    if isinstance(labels, str):
        labels = [x.strip() for x in labels.split(",") if x.strip()]
    payload: dict = {"title": title}
    if body:
        payload["body"] = body
    if labels:
        payload["labels"] = labels
    try:
        import httpx

        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                f"https://api.github.com/repos/{repo}/issues",
                headers={"Authorization": f"Bearer {token}",
                         "Accept": "application/vnd.github+json",
                         "X-GitHub-Api-Version": "2022-11-28"},
                json=payload,
            )
    except Exception as e:  # noqa: BLE001
        return tool_error("GitHub issue", e)
    if r.status_code == 201:
        data = r.json()
        return f"Filed issue #{data.get('number')} in {repo}, sir: “{title}”. {data.get('html_url', '')}"
    if r.status_code in (401, 403):
        return ("GitHub rejected the token, sir — check the PAT has Issues:read/write on that repo.")
    if r.status_code == 404:
        return f"I couldn't find the repo '{repo}', sir — check JARVIS_GITHUB_REPO (owner/repo)."
    return f"GitHub wouldn't create the issue, sir (HTTP {r.status_code}): {clip(r.text, 160)}"


SCHEMAS = [
    {"type": "function", "function": {
        "name": "read_source",
        "description": "Read one of your own project source files (repo-relative path). Use to "
                       "inspect your code before changing it. Secrets are blocked.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Repo-relative path, e.g. src/jarvis/brain/agent.py"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_source",
        "description": "Write/replace one of your own source files (repo-relative). For self-"
                       "improvement. Confirm the change first; then run_tests, then git_commit so it's "
                       "reversible. Secrets and paths outside the repo are refused.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Repo-relative path."},
            "content": {"type": "string", "description": "The full new file contents."}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "list_source",
        "description": "List the files and folders in one directory of the owner's repo "
                       "(repo-relative). Use to explore the codebase before read_source, and for "
                       "'what's in the tools folder'.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Repo-relative folder; default project root."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "run_tests",
        "description": "Run the full Jarvis test suite (bench/run_all_tests.py). ALWAYS do this after "
                       "editing code and before committing — only commit when it's green.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "lint",
        "description": "Run ruff over the code (default: src and bench) and report what it flags. "
                       "Use after write_source edits, and for 'lint it', 'check the style'. Static "
                       "checks only — run_tests runs the actual suite.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Optional path(s) to lint."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "git_status",
        "description": "Show the working-tree status of the owner's repo: current branch plus "
                       "changed and untracked files. Use for 'what have I changed', 'what's "
                       "uncommitted', and before a commit.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "git_diff",
        "description": "Show the line-by-line diff of uncommitted changes, optionally for one path. "
                       "Use for 'what exactly changed', 'show me the diff'. git_status lists WHICH "
                       "files changed; this shows WHAT changed inside them.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Optional repo-relative path."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "git_log",
        "description": "Show recent commits, one per line (default 10, max 50). Use for 'what did I "
                       "commit', 'show recent commits', or to find the commit hash git_revert needs.",
        "parameters": {"type": "object", "properties": {
            "n": {"type": "integer", "description": "How many commits (default 10)."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "git_new_branch",
        "description": "Create a branch and switch to it, keeping master clean before a "
                       "self-improvement change. Use for 'start a new branch', 'branch for X'. "
                       "Call this BEFORE write_source when editing your own code.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Branch name, e.g. improve/faster-recall."}},
            "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "git_commit",
        "description": "Commit your changes (reversible). Stages the named paths, or all changes if "
                       "none given. Confirm with the owner first; only commit green code.",
        "parameters": {"type": "object", "properties": {
            "message": {"type": "string", "description": "Commit message (what changed and why)."},
            "paths": {"type": "array", "items": {"type": "string"}, "description": "Optional specific paths."}},
            "required": ["message"]}}},
    {"type": "function", "function": {
        "name": "git_push",
        "description": "Push the current branch to GitHub's origin remote. OUTWARD-FACING — confirm "
                       "with the owner first. Needs an 'origin' remote configured. Use for 'push "
                       "it', 'push to GitHub'.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "git_revert",
        "description": "Undo a commit by creating a NEW revert commit (history is preserved, nothing "
                       "is lost). Use to roll back a change that went wrong.",
        "parameters": {"type": "object", "properties": {
            "commit": {"type": "string", "description": "Commit hash or ref (default HEAD)."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "create_github_issue",
        "description": "File a GitHub issue in ONE step — use directly when the owner says 'open an "
                       "issue', 'file a bug', 'create a GitHub issue about X'. No need for "
                       "composio_find_tools; this uses the configured PAT. Outward-facing — confirm first.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "The issue title (required)."},
            "body": {"type": "string", "description": "The issue body / description (optional)."},
            "labels": {"type": "array", "items": {"type": "string"},
                       "description": "Optional labels, e.g. ['bug','p1']."},
            "repo": {"type": "string", "description":
                     "Optional 'owner/repo' override (default JARVIS_GITHUB_REPO)."}},
            "required": ["title"]}}},
]

HANDLERS = {
    "read_source": read_source, "write_source": write_source, "list_source": list_source,
    "run_tests": run_tests, "lint": lint,
    "git_status": git_status, "git_diff": git_diff, "git_log": git_log,
    "git_new_branch": git_new_branch, "git_commit": git_commit, "git_push": git_push,
    "git_revert": git_revert, "create_github_issue": create_github_issue,
}
