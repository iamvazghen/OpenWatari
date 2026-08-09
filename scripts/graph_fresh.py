"""How stale is graphify-out/graph.json, measured against the WORKING TREE (TODO J0.1).

The documented check was `git rev-parse HEAD` compared against the graph's recorded commit. That
gives a FALSE GREEN: the graph is rebuilt by the post-commit hook, so its commit always matches
HEAD the moment it is written — while an entire session of uncommitted work sits invisible to it.
On 2026-08-08 that check read "fresh" with 67 modified files, including agent.py and config.py.

So this compares mtimes of the files actually on disk, which is the only thing the graph was built
from. It PRINTS the size of the gap rather than gating on it: this repo commits rarely by policy,
so a stale graph is the normal state, and a check that is red by design is one people stop reading.
An accurate number replaces a false green; that was the defect.

    python scripts/graph_fresh.py           # human summary
    python scripts/graph_fresh.py --quiet   # one line, for preflight

Exit 0 when the graph is at least as new as every source file, 1 otherwise — so it can gate in a
context where that is the right call (a release build), without gating day-to-day work.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "graphify-out" / "graph.json"


def _tracked_sources() -> list[Path]:
    """Files git knows about that the graph is built from. `git ls-files` rather than a glob so
    the answer matches what a fresh clone would contain — and so ignored build output, caches and
    the graph's own directory cannot make the tree look newer than it is."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        return []
    exts = {".py", ".ts", ".tsx", ".js", ".sh", ".ps1", ".toml", ".md"}
    files = []
    for rel in out.stdout.split("\0"):
        if not rel:
            continue
        p = ROOT / rel
        # bench/ is excluded from the graph by .graphifyignore (J8.3), so a test edit does not
        # make the graph stale — counting it would report staleness no rebuild could clear.
        if rel.startswith("bench/") or rel.startswith("graphify-out/"):
            continue
        if p.suffix.lower() in exts and p.is_file():
            files.append(p)
    return files


def main() -> int:
    quiet = "--quiet" in sys.argv
    if not GRAPH.exists():
        print("graph: MISSING (graphify-out/graph.json) — run: graphify update .")
        return 1

    graph_mtime = GRAPH.stat().st_mtime
    newer = [p for p in _tracked_sources() if p.stat().st_mtime > graph_mtime]

    if not newer:
        print("graph: fresh (no tracked source file is newer than graph.json)")
        return 0

    newest = max(newer, key=lambda p: p.stat().st_mtime)
    lag_min = (newest.stat().st_mtime - graph_mtime) / 60
    print(f"graph: STALE — {len(newer)} source file(s) newer than graph.json "
          f"(newest: {newest.relative_to(ROOT).as_posix()}, +{lag_min:.0f} min) — "
          f"rebuild: graphify update .")
    if not quiet:
        for p in sorted(newer, key=lambda p: -p.stat().st_mtime)[:10]:
            print(f"    {p.relative_to(ROOT).as_posix()}")
        if len(newer) > 10:
            print(f"    ... and {len(newer) - 10} more")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
