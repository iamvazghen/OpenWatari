"""One memory origin per host — SYSTEMS.md 30.F1 (hermetic, temp dirs, no network).

**What was actually broken.** Afon's durable state lived under two roots that answer to different
questions. `afon_*.sqlite`, `memory/learned` and `memory/journal` resolved as
`Path(__file__).parents[3]` — *wherever this code was unpacked* — while patterns, the relationship
model and the voiceprint resolved from the home directory. `scripts/deploy_vps.sh` untars
`src/afon` into the brain host's own directory, so the repo root there is a different directory
from the laptop's, and the two hosts kept two sets of learned facts under one name. Neither host
could tell: both paths resolve, both stores open, and each answers confidently from its half.

So this is not a sync problem and a sync would not have fixed it. It is a store keyed on the
location of the code. The fix is `settings.state_dir` (`AFON_STATE_DIR`, default `~/.afon`), one
root, resolved in one module.

Four things have to hold, and each has failed at least once while this was being written:

  1. nothing computes a state path privately — 28 modules spelled `Path.home() / ".afon"`, which
     is 28 chances to drift and no way to point the whole brain at another disk;
  2. every store answers to the ONE setting, so "which environment" is a single answer;
  3. no legacy in-repo location still holds data — a second candidate is the bug itself;
  4. the migration MOVES. A copy leaves both stores in place, which is the state we are ending,
     and it must never overwrite a destination that already has data.

    uv run python bench/test_memory_single_origin.py
"""

from __future__ import annotations

import ast
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


#: The one module allowed to know where state lives. Everyone else asks it.
OWNER = "shared/paths.py"

#: Store basenames that used to be resolved against the repo root.
STORE_FILES = ("afon_graph.sqlite", "afon_vectors.sqlite", "afon_tasks.sqlite",
               "afon_coaching.sqlite", "afon_presence.sqlite", "afon_jobs.sqlite")


def private_state_roots() -> list[str]:
    """Modules that compute a state root themselves instead of asking `shared/paths`.

    AST, not grep: every one of these modules has a docstring saying where its file lives by
    default, and a grep for `~/.afon` flags the sentence that documents the rule alongside the code
    that breaks it. A `Path.home() / ".afon"` is a BinOp; a docstring is not.
    """
    src = ROOT / "src" / "afon"
    out: list[str] = []
    for p in sorted(src.rglob("*.py")):
        rel = p.relative_to(src).as_posix()
        if rel == OWNER:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if not isinstance(n, ast.BinOp) or not isinstance(n.op, ast.Div):
                continue
            right = n.right
            if not (isinstance(right, ast.Constant) and isinstance(right.value, str)):
                continue
            piece = right.value
            if piece == ".afon" or piece in STORE_FILES:
                out.append(f"{rel}:{n.lineno}  ...  / {piece!r}")
    return out


def main() -> None:
    from afon.config import settings
    from afon.shared.paths import (LEGACY_STATE, REPO_ROOT, memory_dir, migrate_repo_state,
                                   second_origins, state_dir, store_path)

    print("[1] one module knows where state lives")
    strays = private_state_roots()
    check(f"no module outside {OWNER} computes a state root", not strays, str(strays))
    check("the repo root is not a state root",
          state_dir() != REPO_ROOT and REPO_ROOT not in state_dir().parents,
          f"state_dir()={state_dir()} — state under the repo forks per checkout")

    print("\n[2] every store answers to the ONE setting")
    # The load-bearing check. A store that keeps its own default is a store that stays behind when
    # the state root moves, and it stays behind silently — it opens a fresh, empty database and
    # answers from it. Driven rather than grepped, because the failure is in what the function
    # RETURNS, and a store can import `state_dir` and then not use it.
    import afon.brain.coaching as coaching
    import afon.brain.graph as graph
    import afon.brain.memory as memory
    import afon.brain.presence as presence
    import afon.brain.scheduler as scheduler
    import afon.brain.semantic as semantic
    import afon.brain.tasks as tasks

    overrides = ("state_dir", "memory_graph_db_path", "memory_vector_db_path", "tasks_db_path",
                 "coaching_db_path", "presence_db_path", "scheduler_db_path",
                 "session_persist_path")
    saved = {k: getattr(settings, k, None) for k in overrides}
    try:
        with tempfile.TemporaryDirectory() as td:
            for k in overrides:
                setattr(settings, k, None)          # a personal .env must not decide this test
            settings.state_dir = td
            root = Path(td)
            resolved = {
                "graph": graph._graph_db_path(),
                "vectors": semantic._vector_db_path(),
                "tasks": tasks._db_path(),
                "coaching": coaching._db_path(),
                "presence": presence._db_path(),
                "jobs": Path(scheduler._db_url().replace("sqlite:///", "")),
                "memory (L1/L2)": memory.MemoryStore().base,
            }
            for label, p in resolved.items():
                check(f"{label} follows the state root", root in p.parents,
                      f"{p} — moving AFON_STATE_DIR leaves this store behind, silently empty")
            check("store_path agrees with the stores it is supposed to place",
                  {store_path(n) for n in ("graph", "vectors", "tasks", "coaching", "presence")}
                  == {resolved[n] for n in ("graph", "vectors", "tasks", "coaching", "presence")})
            check("the memory dir is one directory, not one per caller",
                  memory.MemoryStore().base == memory_dir())
            # Two stores used to take their DIRECTORY from `settings.tasks_db_path`'s parent, so
            # moving the task queue to another disk moved coaching and presence with it.
            settings.tasks_db_path = str(root / "elsewhere" / "afon_tasks.sqlite")
            check("pointing the task queue elsewhere does not drag other stores along",
                  coaching._db_path().parent == root and presence._db_path().parent == root,
                  f"coaching={coaching._db_path()}  presence={presence._db_path()}")
    finally:
        for k, v in saved.items():
            setattr(settings, k, v)

    print("\n[3] no second candidate on this host")
    extra = second_origins()
    check("no legacy in-repo location still holds state", not extra,
          f"{[str(p) for p in extra]} — run `uv run python -m afon.shared.paths --migrate`")

    print("\n[4] the migration moves, once, and never overwrites")
    with tempfile.TemporaryDirectory() as td:
        fake_repo, fake_state = Path(td) / "repo", Path(td) / "state"
        (fake_repo / "memory" / "learned").mkdir(parents=True)
        (fake_repo / "memory" / "learned" / "a.md").write_text("fact", encoding="utf-8")
        (fake_repo / "afon_graph.sqlite").write_text("db", encoding="utf-8")
        (fake_repo / "afon_tasks.sqlite").write_text("", encoding="utf-8")   # empty = not state
        (fake_repo / "memory" / "journal").mkdir()                            # empty dir = not state
        settings.state_dir = str(fake_state)
        try:
            moved = migrate_repo_state(fake_repo, dry_run=True)
            check("a dry run reports without touching anything",
                  (fake_repo / "afon_graph.sqlite").exists() and not fake_state.exists()
                  and any("would move" in m for m in moved), str(moved))
            migrate_repo_state(fake_repo)
            check("the file MOVED — it is not in the repo any more",
                  not (fake_repo / "afon_graph.sqlite").exists(),
                  "a copy leaves two stores, which is the bug this closes")
            check("...and it arrived intact",
                  (fake_state / "afon_graph.sqlite").read_text(encoding="utf-8") == "db")
            check("a learned note came with it",
                  (fake_state / "memory" / "learned" / "a.md").read_text(encoding="utf-8") == "fact")
            check("an empty leftover is not mistaken for state",
                  (fake_repo / "afon_tasks.sqlite").exists() and not (fake_state / "afon_tasks.sqlite").exists())
            check("migrating leaves no second origin behind", not second_origins(fake_repo),
                  str([str(p) for p in second_origins(fake_repo)]))
            check("it records what it did", (fake_state / "MIGRATED.txt").is_file())
            check("running it again is a no-op", migrate_repo_state(fake_repo) == [])

            # The destination the ROLLOUT itself creates: start the brain once before migrating and
            # it opens the new location, writes a schema, and leaves an empty database sitting in
            # the way. A plain `dest.exists()` then refuses to migrate the real data behind it —
            # forever, on every host that has been started. This is not hypothetical; it happened
            # on this laptop twenty minutes after the change was written.
            import sqlite3 as _sq
            (fake_repo / "afon_presence.sqlite").write_text("real data", encoding="utf-8")
            fresh = fake_state / "afon_presence.sqlite"
            _c = _sq.connect(fresh)
            _c.execute("CREATE TABLE activity (ts TEXT)")     # schema, no rows — what a boot leaves
            _c.commit()
            _c.close()
            migrate_repo_state(fake_repo)
            check("an empty database left by a first boot does not block the migration",
                  fresh.read_text(encoding="utf-8", errors="ignore") == "real data",
                  "the store the brain created on startup is an artifact, not a store")
            check("...and the source is gone, so there is still only one",
                  not (fake_repo / "afon_presence.sqlite").exists())

            # Both sides populated: the case that actually happens on a host that ran the old code
            # after a partial migration. Overwriting here would delete weeks of the winner's facts.
            (fake_repo / "afon_graph.sqlite").write_text("older", encoding="utf-8")
            report = migrate_repo_state(fake_repo)
            check("a destination that already exists is never overwritten",
                  (fake_state / "afon_graph.sqlite").read_text(encoding="utf-8") == "db",
                  "the in-repo copy is the STALE one; clobbering the state root loses the live store")
            check("...and the collision is reported rather than swallowed",
                  any("KEPT BOTH" in m for m in report), str(report))
        finally:
            settings.state_dir = saved["state_dir"]

    print("\n[5] a deploy cannot carry state to the other host")
    # How the split arrived in the first place: whatever the deploy ships lands in the brain host's
    # repo root, so the moment a store lives there, deploying either overwrites the VPS's memory
    # with the laptop's or leaves two. Neither is a thing anyone would choose on purpose.
    deploy = (ROOT / "scripts" / "deploy_vps.sh").read_text(encoding="utf-8")
    tar_lines = [ln for ln in deploy.splitlines() if ln.strip().startswith("tar ") and "-czf -" in ln]
    check("the deploy has exactly one tar of the source tree", len(tar_lines) == 1, str(tar_lines))
    shipped = tar_lines[0] if tar_lines else ""
    for name in ("memory", *STORE_FILES):
        check(f"the deploy does not ship {name}", f" {name}" not in shipped, shipped)
    check("every legacy state location is named in one table",
          len(LEGACY_STATE) >= len(STORE_FILES) + 2,
          "a location the migration does not know about is a location it leaves behind")

    merge_is_a_union()

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


# ── 30.F1, the half a human has to decide: which host wins ───────────────────────────────────
# For derived stores (vectors, presence, coaching) the brain host wins and the other copy is
# discarded — they regenerate. Learned facts are not derived: a fact only one host was ever told
# is not stale, it is the only copy, and "newest wins" would delete it. So the notes are UNIONED.
# These checks hold `scripts/merge_memory.py` to that, because the difference between a union and
# an overwrite is invisible until the day someone notices Afon has forgotten something.
def merge_is_a_union() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("merge_memory", ROOT / "scripts" / "merge_memory.py")
    mm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mm)

    from afon.brain.memory import MemoryStore

    print("\n[30.F1] merging two hosts' memories adds, never replaces")
    with tempfile.TemporaryDirectory() as td:
        a, b = Path(td) / "laptop", Path(td) / "vps"
        laptop, vps = MemoryStore(a), MemoryStore(b)
        laptop.remember("the rabbit farm is in Armenia", ["farm"], source="owner")
        laptop.remember("he prefers short answers", source="inferred")
        vps.remember("the rabbit farm is in Armenia", ["farm"], source="owner")   # both know this
        vps.remember("he prefers short answers", source="owner")                  # stronger here
        vps.remember("the deadline moved to Friday", ["work"], source="owner")    # only the VPS
        (b / "journal").mkdir(parents=True, exist_ok=True)
        # One line both hosts have, one only the other host has. Both are needed: without the
        # second the merge finds nothing missing and returns before it writes anything, so the
        # append-vs-overwrite checks below pass without ever exercising the write.
        (b / "journal" / "2026-08-01.md").write_text(
            "- talked about the farm\n- and the vet called\n", encoding="utf-8")
        (a / "journal").mkdir(parents=True, exist_ok=True)
        (a / "journal" / "2026-08-01.md").write_text("- talked about the farm\n- and the deadline\n",
                                                     encoding="utf-8")

        before = laptop.count()
        plan, days = mm.merge(b, a, apply=False)
        check("a dry run writes nothing", laptop.count() == before, f"{before} -> {laptop.count()}")
        check("it plans the fact only the other host has",
              any("deadline moved" in line for line in plan), str(plan))
        check("it does not plan to re-add a fact both hosts know",
              sum("rabbit farm" in line for line in plan) == 0, str(plan))
        check("it plans to PROMOTE the fact the other host heard first-hand",
              any(line.startswith("^") and "short answers" in line for line in plan), str(plan))

        mm.merge(b, a, apply=True)
        texts = {n.text: n for n in MemoryStore(a)._iter_notes()}
        check("the other host's unique fact is now known here", "the deadline moved to Friday" in texts)
        check("the shared fact was not duplicated",
              sum("rabbit farm" in t for t in texts) == 1, str(list(texts)))
        check("the guess was promoted to what the owner actually said",
              texts["he prefers short answers"].source == "owner",
              "a merge that keeps the weaker provenance throws away the moment it stopped being a guess")
        check("nothing was lost from this host", laptop.count() >= before)
        merged_journal = (a / "journal" / "2026-08-01.md").read_text(encoding="utf-8")
        check("a journal line this host already had is not duplicated",
              merged_journal.count("talked about the farm") == 1, merged_journal)
        check("the other host's journal line arrived", "and the vet called" in merged_journal,
              merged_journal)
        check("...and this host's own line survives", "and the deadline" in merged_journal,
              "appending is the only safe direction: the other file is not a newer version of this "
              "one, it is a different day's worth of a different conversation")
        check("re-running the merge changes nothing", mm.merge(b, a, apply=False) == ([], []),
              "a merge that is not idempotent cannot be run twice, and it will be")

    # A real merge writes hundreds of facts inside one second, which one fact per conversation never
    # does. The filename is timestamp + a TRUNCATED slug, so two different facts collided and the
    # second overwrote the first — silently, and only visible because the merge then refused to
    # converge: the same fact was reported missing on every re-run, written, and immediately
    # replaced again. 719 facts arrived where 720 were sent.
    print("\n[30.F1] a bulk merge does not lose a fact to a filename collision")
    with tempfile.TemporaryDirectory() as td:
        one, two = Path(td) / "a", Path(td) / "b"
        far = MemoryStore(two)
        # Same first eight words, different meaning — this is the real pair that went missing.
        far.remember("the owner sometimes prefers not to talk and appreciates quiet assistance")
        far.remember("the owner sometimes prefers not to talk and appreciates quiet background help")
        mm.merge(two, one, apply=True)
        landed = {n.text for n in MemoryStore(one)._iter_notes()}
        check("both facts survive the same-second write", len(landed) == 2, str(landed))
        check("...and the merge converges", mm.merge(two, one, apply=False) == ([], []),
              "a fact that cannot be stored is reported missing forever")

    # Two hosts merge in BOTH directions — the laptop pulls the brain host's facts, the brain host
    # takes the laptop's. The provenance marker the merge writes is itself a line, so counting it as
    # content made each side append a marker about the other's marker on every run: day files that
    # grow forever and a merge that never reports "nothing to do".
    print("\n[30.F1] merging in both directions terminates")
    with tempfile.TemporaryDirectory() as td:
        one, two = Path(td) / "a", Path(td) / "b"
        for d, line in ((one, "- the laptop's afternoon\n"), (two, "- the brain host's evening\n")):
            (d / "journal").mkdir(parents=True)
            (d / "journal" / "2026-08-01.md").write_text(line, encoding="utf-8")
        for _ in range(3):
            mm.merge(two, one, apply=True)
            mm.merge(one, two, apply=True)
        check("neither side still has anything to give the other",
              mm.merge(two, one, apply=False) == ([], []) and mm.merge(one, two, apply=False) == ([], []))
        text = (one / "journal" / "2026-08-01.md").read_text(encoding="utf-8")
        check("both days' lines are present exactly once",
              text.count("afternoon") == 1 and text.count("evening") == 1, text)


if __name__ == "__main__":
    main()
