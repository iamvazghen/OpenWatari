"""50.F1 — an export a person can read with no codebase, proved by reading it without one.

The substrate for continuity existed: a vault, backups, checkpoints, an audit trail. What did not
exist was the property they are supposed to deliver. A backup is a tar of this program's private
layout — sqlite whose schema lives in the code, a `learned` tree whose meaning is a docstring, a
JSONL whose fields are named in a dataclass. Restoring it needs Afon, which is the opposite of
preservation.

The load-bearing check here is [3]. It shells out to a **fresh interpreter with `afon` removed from
the path** and has it read the export end to end. Asserting readability from inside a process that
has already imported the package proves nothing at all: every helper it would reach for is exactly
the thing that is supposed to be unnecessary.

  [written]     memory, journal, documents, state and an audit SUMMARY, each as markdown or JSON;
  [honest]      a store that was not there is named in the manifest as absent, not omitted;
  [no codebase] a fresh process with no access to `afon` reads the manifest, verifies every hash,
                and finds the facts in plain text;
  [damaged]     a tampered file fails verification rather than passing quietly.

Hermetic: a temporary state root. No network.

    uv run python bench/test_portable_export.py
"""

from __future__ import annotations

import json
import subprocess
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


#: A reader written the way a stranger in ten years would write one: standard library only, and the
#: package deliberately unreachable. If this script can do it, the export is portable.
READER = r"""
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
m = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
bad = [f["path"] for f in m["files"]
       if hashlib.sha256((root / f["path"]).read_bytes()).hexdigest() != f["sha256"]]
facts = (root / "memory" / "learned.md").read_text(encoding="utf-8")
state = json.loads((root / "state" / "world_model.json").read_text(encoding="utf-8"))
print(json.dumps({
    "format": m["format"],
    "bad": bad,
    "has_fact": "water system" in facts,
    "goal": state.get("goal"),
    "absent": m["absent"],
    "readme": "text editor" in (root / "README.md").read_text(encoding="utf-8"),
}))
"""


def _seed(state: Path) -> None:
    learned = state / "memory" / "learned"
    learned.mkdir(parents=True)
    (learned / "a.md").write_text("The farm needs a new water system by spring\nsource: told you",
                                  encoding="utf-8")
    (learned / "b.md").write_text("He trains on Mondays\nsource: observed", encoding="utf-8")
    journal = state / "memory" / "journal"
    journal.mkdir(parents=True)
    (journal / "2026-09-12.md").write_text("- Reviewed the farm numbers.", encoding="utf-8")
    (state / "world_model.json").write_text('{"goal": "ship the assistant"}', encoding="utf-8")
    (state / "routines.json").write_text('{"morning": "07:30"}', encoding="utf-8")
    docs = state / "documents"
    docs.mkdir(parents=True)
    (docs / "index.jsonl").write_text(
        json.dumps({"title": "Feed order", "kind": "note", "where": "the vault at Afon/Feed.md",
                    "ref": "x", "at": 1789000000.0, "verified": True}) + "\n"
        + json.dumps({"title": "Half-written brief", "kind": "brief", "where": "Notion",
                      "ref": "p-1", "at": 1789000001.0, "verified": False}) + "\n",
        encoding="utf-8")
    audit = state / "audit"
    audit.mkdir(parents=True)
    (audit / "2026-09-12.jsonl").write_text(
        '{"tool": "search_vault"}\n{"tool": "search_vault"}\n{"tool": "send_telegram"}\n',
        encoding="utf-8")


def main() -> None:
    from afon.protocols import portable as P

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        state = Path(d) / "state"
        state.mkdir()
        _seed(state)
        dest = Path(d) / "export"
        out = P.export(dest, state=state, memory=state / "memory", audit=state / "audit")

        print("[written] 50.F1 — everything worth keeping, in plain formats")
        check("the export succeeded", out.ok, out.error)
        for rel in ("README.md", "MANIFEST.json", "memory/learned.md", "memory/journal.md",
                    "documents.md", "audit-summary.md", "state/world_model.json"):
            check(f"{rel} is written", (dest / rel).is_file())
        check("nothing binary is written",
              all(p.suffix in (".md", ".json", ".jsonl") for p in dest.rglob("*") if p.is_file()),
              [p.name for p in dest.rglob("*") if p.is_file() and p.suffix not in
               (".md", ".json", ".jsonl")])
        facts = (dest / "memory" / "learned.md").read_text(encoding="utf-8")
        check("every fact is there, not a sample", "water system" in facts and "Mondays" in facts)
        check("...with when it was kept", "kept 20" in facts, facts[:200])
        check("the journal keeps its days apart", "## 2026-09-12" in
              (dest / "memory" / "journal.md").read_text(encoding="utf-8"))
        docs = (dest / "documents.md").read_text(encoding="utf-8")
        check("documents carry where they went", "the vault at Afon/Feed.md" in docs)
        check("...and an unconfirmed one still says so", "never confirmed" in docs, docs[-200:])

        print("\n[written] the audit is summarised, not copied")
        summary = (dest / "audit-summary.md").read_text(encoding="utf-8")
        check("tool counts are there", "search_vault — 2" in summary, summary)
        check("...and the raw values are not",
              "jsonl" not in summary.lower() and '"args"' not in summary)
        check("the export says why the raw log is left out",
              "values those tools were given" in summary, summary[:400])
        check("the counts are reported", out.counts.get("facts") == 2 and
              out.counts.get("tool_calls") == 3, out.counts)

        print("\n[honest] a store that was not there is named, not dropped")
        manifest = json.loads((dest / "MANIFEST.json").read_text(encoding="utf-8"))
        check("objectives.json was absent and is listed",
              any("objectives.json" in a for a in manifest["absent"]), manifest["absent"])
        check("...in the owner's words, not the filename alone",
              any("objectives" in a and "(" in a for a in manifest["absent"]), manifest["absent"])
        check("a store that WAS there is not listed as absent",
              not any("world_model" in a for a in manifest["absent"]), manifest["absent"])
        check("the manifest hashes every file it lists",
              all(len(f["sha256"]) == 64 for f in manifest["files"]), manifest["files"][:1])
        check("...and every written file is listed",
              {f["path"] for f in manifest["files"]} == {
                  p.relative_to(dest).as_posix() for p in dest.rglob("*")
                  if p.is_file() and p.name != "MANIFEST.json"},
              sorted({p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}))
        check("the format is stamped", manifest["format"] == P.FORMAT, manifest["format"])

        print("\n[no codebase] a fresh process with no `afon` on its path reads the whole thing")
        # The load-bearing check. `-I` isolates the interpreter and the cwd is elsewhere, so the
        # reader cannot reach this repo even by accident.
        proc = subprocess.run([sys.executable, "-I", "-c", READER, str(dest)],
                              capture_output=True, text=True, cwd=d, timeout=60)
        check("the reader ran at all", proc.returncode == 0, proc.stderr[-400:])
        if proc.returncode == 0:
            got = json.loads(proc.stdout)
            check("it read the format", got["format"] == P.FORMAT, got)
            check("every checksum matched", got["bad"] == [], got["bad"])
            check("it found a fact in plain text", got["has_fact"], got)
            check("it read the structured state", got["goal"] == "ship the assistant", got)
            check("it could see what was missing", got["absent"], got)
            check("the README tells a stranger how to open it", got["readme"], got)

        print("\n[damaged] a tampered copy fails rather than passing quietly")
        check("a clean export verifies", P.verify(dest)["ok"], P.verify(dest))
        (dest / "memory" / "learned.md").write_text("nothing here", encoding="utf-8")
        bad = P.verify(dest)
        check("an edited file is caught", not bad["ok"], bad)
        check("...and named", "learned.md" in bad.get("error", ""), bad.get("error"))
        (dest / "MANIFEST.json").unlink()
        gone = P.verify(dest)
        check("a missing manifest is not a pass", not gone["ok"], gone)
        check("...and says so plainly", "manifest" in gone.get("error", "").lower(), gone)

        print("\n[damaged] the drill exports into an EMPTY directory, never in place")
        # Verifying the live directory would pass on last run's files, which is the failure a drill
        # is for. So the drill makes its own and throws it away.
        src = (ROOT / "src/afon/protocols/portable.py").read_text(encoding="utf-8")
        body = src.split("def run_export_drill", 1)[1]
        check("the drill uses a throwaway directory", "mkdtemp" in body)
        check("...and cleans it up", "rmtree" in body)
        check("...and files its verdict", "record_to" in body and "write_text" in body)

        print("\n[damaged] it is on a schedule, not a command someone remembers")
        sched = (ROOT / "src/afon/brain/scheduler.py").read_text(encoding="utf-8")
        check("a job exists", "_fire_portable_drill" in sched)
        check("...and is registered daily", "schedule_portable_drill" in sched)
        server = (ROOT / "src/afon/brain/server.py").read_text(encoding="utf-8")
        check("...and the brain actually starts it", "schedule_portable_drill()" in server)
        drill_body = sched.split("async def _fire_portable_drill", 1)[1].split("\nasync def ")[0]
        check("it speaks up only on failure", drill_body.count("_emit_proactive") == 1,
              "a drill that reports every success teaches the owner to ignore it")
        check("...and a success is a log line, not an interruption",
              "logger.info" in drill_body and drill_body.index("logger.info") <
              drill_body.index("_emit_proactive"))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
