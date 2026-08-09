"""Phase 13 — coding & self-improvement tools + skills, hermetic.

Verifies the safety rails that make self-improvement trustworthy: file I/O is confined to the repo
and refuses secrets; only reversible git ops exist (no reset/force-push tool is registered); real
file I/O round-trips against the repo; skills load from skills/; and writes/commits/pushes are
confirm-gated. Read-only against git; no network.

Since 2026-07-31 the tool-level entry points (`read_source`, `write_source`, `git_*`) no longer touch
this host's repo — they forward to the laptop over PC_LINK, because on the VPS the local tree is a
deploy artefact whose git points at the real remote. The *real* file I/O therefore lives in the
executor handlers (`_pc_repo_read` / `_pc_repo_write`), and that is what this file exercises for the
round-trip. Routing itself, and the refusal when no laptop is linked, are pinned by
`test_coding_routing.py`.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f" — {detail}" if detail and not ok else ""))


def main() -> None:
    import afon.brain.tools.coding as coding
    import afon.brain.tools.skills as skills
    from afon.brain.proactive import confirm_required
    from afon.brain.tools import tool_names

    print("[1] path safety — repo-relative only, secrets blocked")
    check("a normal source path resolves", coding._safe_path("src/afon/config.py") is not None)
    check("traversal outside repo is refused", coding._safe_path("../../etc/passwd") is None)
    check("absolute escape is refused", coding._safe_path("C:/Windows/system32") is None)
    check(".env is blocked", coding._safe_path(".env") is None)
    check("a .session file is blocked", coding._safe_path("afon.session") is None)
    check("voiceprint.json is blocked", coding._safe_path("voiceprint.json") is None)
    check("the audit/ dir is blocked", coding._safe_path("audit/2026-06-12.jsonl") is None)
    check(".git internals are blocked", coding._safe_path(".git/config") is None)

    print("\n[2] the executor reads real code; the tool refuses secrets before they leave the brain")
    # The executor is where the file actually lives, so that is where the read is proven.
    got = json.loads(asyncio.run(coding._pc_repo_read({"path": "src/afon/config.py"})))
    check("reads config.py", got["ok"] and "Settings" in got["out"], str(got)[:60])
    secret = asyncio.run(coding.read_source({"path": ".env"}))
    check("refuses to read .env", "outside the project or a protected file" in secret, secret)
    blocked = json.loads(asyncio.run(coding._pc_repo_read({"path": ".env"})))
    check("...and the executor refuses it too, independently", blocked["ok"] is False, str(blocked))

    print("\n[3] write round-trips inside the repo (temp file, then cleaned)")
    rel = "bench/_p13_scratch.tmp"
    res = json.loads(asyncio.run(coding._pc_repo_write({"path": rel, "content": "scratch 123"})))
    p = Path(__file__).resolve().parents[1] / rel
    check("write reports success", res["ok"] is True, str(res))
    check("file exists with content", p.is_file() and p.read_text() == "scratch 123")
    bad = asyncio.run(coding.write_source({"path": "../escape.tmp", "content": "x"}))
    check("write outside repo refused", "won't write there" in bad, bad)
    worse = json.loads(asyncio.run(coding._pc_repo_write({"path": "../escape.tmp", "content": "x"})))
    check("...and refused at the executor as well", worse["ok"] is False, str(worse))
    p.unlink(missing_ok=True)

    print("\n[4] git read ops run through the executor against the real repo")
    out = json.loads(asyncio.run(coding._pc_repo_exec(
        {"argv": ["git", "status", "--short", "--branch"], "timeout": 30})))
    check("git_status returns text", out["rc"] == 0 and out["out"].strip() != "", str(out)[:80])
    out = json.loads(asyncio.run(coding._pc_repo_exec(
        {"argv": ["git", "log", "-3", "--oneline"], "timeout": 30})))
    check("git_log returns commits", out["rc"] == 0 and out["out"].strip() != "", str(out)[:80])

    print("\n[5] only reversible git tools exist — no destructive ones")
    names = set(tool_names())
    want = {"git_status", "git_diff", "git_log", "git_new_branch", "git_commit", "git_push",
            "git_revert", "read_source", "write_source", "list_source", "run_tests", "lint"}
    check("all coding tools registered", want <= names, str(sorted(want - names)))
    forbidden = {"git_reset", "git_force_push", "git_rebase", "git_clean", "git_amend",
                 "git_branch_delete", "git_push_force"}
    check("no destructive git tool is registered", not (forbidden & names), str(forbidden & names))

    print("\n[6] writes/commits/pushes are confirm-gated; reads are not")
    check("write_source confirm-gated", confirm_required("write_source"))
    check("git_commit confirm-gated", confirm_required("git_commit"))
    check("git_push confirm-gated", confirm_required("git_push"))
    check("git_revert confirm-gated", confirm_required("git_revert"))
    check("read_source NOT gated", not confirm_required("read_source"))
    check("git_status NOT gated", not confirm_required("git_status"))

    print("\n[6b] first-class create_github_issue: registered, gated, degrades without a PAT")
    check("create_github_issue registered", "create_github_issue" in tool_names())
    check("create_github_issue has a handler", "create_github_issue" in coding.HANDLERS)
    check("create_github_issue is confirm-gated (outward-facing)",
          confirm_required("create_github_issue"))
    # With no PAT/repo it must degrade to a spoken 'not configured' note, never raise.
    from afon.config import settings as _s
    _tok, _repo = _s.github_token, _s.github_repo
    _s.github_token = None
    _s.github_repo = None
    try:
        msg = asyncio.run(coding.create_github_issue({"title": "x"}))
        check("degrades gracefully without a token", "configured" in msg.lower(), msg[:80])
    finally:
        _s.github_token, _s.github_repo = _tok, _repo

    print("\n[7] skills library loads the coding playbooks")
    listing = asyncio.run(skills.list_skills({}))
    for s in ("self-improvement", "afon-architecture", "python", "adding-a-tool", "task-cleanup"):
        check(f"skill '{s}' listed", s in listing, listing[:80])
    doc = asyncio.run(skills.read_skill({"name": "self-improvement"}))
    check("read_skill returns the playbook", "reversible" in doc.lower(), doc[:80])
    check("unknown skill handled", "don't have" in asyncio.run(skills.read_skill({"name": "zzz"})).lower())
    check("list_skills + read_skill registered", {"list_skills", "read_skill"} <= names)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
