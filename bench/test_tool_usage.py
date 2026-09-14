"""Durable per-tool call counts survive a restart — the missing input to catalogue tiering.

`METRICS.incr(f"tool.{name}")` counted every call in memory only, and the brain restarts daily,
so the store that would answer "which tools does the owner actually use" was reset before it could
ever become evidence. Tiering the catalogue on no data means guessing which tools are rare, and a
wrong guess removes a capability silently.

Hermetic: temp file, no real state dir, no network.

    uv run python bench/test_tool_usage.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.tool_usage import ToolUsage  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    """NB (name, ok) — see the note in test_proactive_suppression.py; the assert is the guard."""
    global passed, failed
    assert isinstance(ok, bool), f"check({name!r}, {ok!r}) — second arg must be a bool; args swapped?"
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}" + (f" -> {detail}" if detail else ""))


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "tool_usage.json"

        print("\n[1] counts accumulate in memory")
        u = ToolUsage(path)
        for _ in range(3):
            u.record("weather")
        u.record("send_email")
        check("weather counted 3", u.counts().get("weather") == 3, str(u.counts()))
        check("send_email counted 1", u.counts().get("send_email") == 1)
        check("total is the sum", u.total() == 4, str(u.total()))

        print("\n[2] THE POINT: the counts survive a restart")
        # The first record of a process flushes on purpose (see ToolUsage.__init__), so the file
        # already exists here. My first version of this check asserted the opposite and failed —
        # the assertion encoded my assumption, not a requirement.
        check("the first record already persisted the store", path.exists())
        u.flush()
        check("file present after an explicit flush", path.exists())
        u2 = ToolUsage(path)
        check("weather still 3 in a fresh process", u2.counts().get("weather") == 3,
              str(u2.counts()))
        check("total preserved", u2.total() == 4)

        print("\n[3] top() ranks by calls, most-used first")
        u2.record("send_email")
        u2.record("send_email")
        u2.record("send_email")
        u2.record("send_email")
        top = u2.top(2)
        check("send_email now ranks first", top[0][0] == "send_email" and top[0][1] == 5, str(top))
        check("top(n) honours n", len(top) == 2, str(top))

        print("\n[4] unused() names only what was never seen")
        never = u2.unused(["weather", "send_email", "browser", "run_protocol"])
        check("never-called tools reported", never == ["browser", "run_protocol"], str(never))
        check("called tools excluded", "weather" not in never)

        print("\n[5] the flush is debounced — the disk is not in the tool latency path")
        u3 = ToolUsage(Path(td) / "debounce.json")
        writes = 0
        real = u3.flush

        def counting_flush() -> None:
            nonlocal writes
            writes += 1
            real()

        u3.flush = counting_flush  # type: ignore[method-assign]
        for _ in range(50):
            u3.record("weather")
        # The invariant is O(1) writes, not zero: one for the deliberate first-record flush, and
        # then nothing for the rest of the debounce window.
        check("50 calls cause exactly 1 write, not 50", writes == 1, f"writes={writes}")
        check("but the counts are all there", u3.counts()["weather"] == 50)

        print("\n[6] a torn write cannot lose the previous data (atomic replace)")
        u2.flush()
        payload = json.loads(path.read_text(encoding="utf-8"))
        check("file is valid JSON with a tools map", isinstance(payload.get("tools"), dict))
        check("no .tmp file left behind", not (path.with_suffix(".json.tmp")).exists())
        check("last-called timestamp recorded",
              bool(payload["tools"]["weather"].get("last")), str(payload["tools"]["weather"]))

        print("\n[7] a corrupt store starts fresh rather than crashing the brain")
        bad = Path(td) / "corrupt.json"
        bad.write_text("{not json", encoding="utf-8")
        u4 = ToolUsage(bad)
        check("corrupt file -> empty store, no exception", u4.counts() == {})
        u4.record("weather", when=datetime(2026, 8, 9, 12, 0))
        u4.flush()
        check("and it can write over the corrupt file", json.loads(
            bad.read_text(encoding="utf-8"))["tools"]["weather"]["calls"] == 1)

        print("\n[8] 03.R4 — the staleness report: what has gone quiet, and is anything testing it")
        from datetime import timedelta

        u5 = ToolUsage(Path(td) / "stale.json")
        now = datetime.now()
        u5.record("weather", when=now)
        u5.record("crypto_price", when=now - timedelta(days=120))
        known = ["weather", "crypto_price", "never_called_tool"]
        stale = dict(u5.stale(known, days=90))
        check("a tool used today is not stale", "weather" not in stale, str(stale))
        check("a tool last used 120 days ago is stale", stale.get("crypto_price") is not None)
        check("...and the report says WHEN, not just that it is stale",
              str(stale.get("crypto_price", "")).startswith(str((now - timedelta(days=120)).year)))
        check("a tool never called is reported as 'never', not omitted",
              stale.get("never_called_tool") == "never",
              "never-called and long-unused are the same decision; hiding one shrinks the report")
        check("an unreadable timestamp counts as stale, not as use",
              "x" not in ToolUsage(Path(td) / "s2.json").stale(["x"], days=90)[0][1].replace(
                  "never", ""))

        # The rule 03.R4 states: a tool that is not being USED must at least be EXERCISED, or it is
        # dead code advertised to the model — catalogue tokens spent on something nothing checks.
        # The usage store is machine-local and young, so "used" cannot be the test on a fresh
        # clone; "exercised by a bench case" can, and it is the half that keeps a retired-in-all-
        # but-name tool from sitting in the registry unnoticed.
        import re as _re

        from afon.brain.tools import tool_names

        bench_dir = Path(__file__).resolve().parent
        corpus = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                           for p in bench_dir.glob("*.py"))
        names = sorted(tool_names())
        unexercised = [n for n in names
                       if not _re.search(r"\b" + _re.escape(n) + r"\b", corpus)]
        print(f"        {len(names) - len(unexercised)}/{len(names)} tools are named by a bench case")
        for n in unexercised:
            print(f"        NOT exercised: {n}")
        check("every registered tool is exercised by at least one bench case",
              not unexercised,
              f"{len(unexercised)} advertised to the model and checked by nothing: {unexercised}")

    print(f"\n  {passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
