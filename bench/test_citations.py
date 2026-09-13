"""41.F2/41.F3 — a source he names is one he opened, and a budget that ran out says so.

A model handed a page of text will attribute a claim to a plausible URL it never fetched. The
failure is invisible in the way that matters most: the answer looks BETTER for carrying a citation,
and the owner has no way to tell a real link from a well-formed one. The research skill has said
"cite the source" since the beginning. Nothing checked.

The other half is the same shape one level up. A research task that ran out of steps was handed
back as a finished answer — the model was told to write its summary, wrote one, and nothing
anywhere said it was a truncated answer to a bigger question.

What this asserts:

  * every page Afon really retrieves is recorded, and the plumbing that fetched it is not;
  * a URL in a reply whose host he never retrieved is named as his own, not as a source;
  * the ledger is per-turn, so yesterday's reading cannot vouch for today's claim;
  * an obstructed page is never citable — he could not read what was behind the wall;
  * a task declares its budget before it works, and says plainly when it stopped early.

Hermetic: the HTTP layer and the LLM are replaced. No network.

    uv run python bench/test_citations.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class _Resp:
    def __init__(self, text):
        self.text = text
        self.status_code = 200

    def json(self):
        return {}

    def raise_for_status(self):
        return None


async def main() -> None:
    from afon.brain import citations as C
    from afon.brain.tools import web

    print("[1] a page he really opened is recorded; the reader that fetched it is not")
    C.start()
    body = ("# Euro area inflation eased in August\n\n"
            + "Prices rose 2.1 percent year on year, the statistics office said. " * 12)
    web._scrape_jina = lambda url: _wrap(body)
    web._scrape_firecrawl = lambda url: _wrap("")
    web.CACHE._store.clear() if hasattr(web.CACHE, "_store") else None

    out = await web.scrape_url({"url": "https://www.reuters.com/markets/euro-cpi"})
    check("the body comes back", "2.1 percent" in out, out[:120])
    check("...and the page is in the ledger", "reuters.com" in C.hosts(), C.hosts())
    check("...under the site, not the reader that proxied it",
          not any("jina" in h for h in C.hosts()), C.hosts())
    check("the page's own title is kept, so a citation is recognisable",
          any("inflation" in s.title.lower() for s in C.sources()), C.sources())

    print("\n[2] an obstructed page is NOT a source")
    C.start()
    web._scrape_jina = lambda url: _wrap("Subscribe to continue reading. " * 3)
    web._scrape_firecrawl = lambda url: _wrap("Subscribe to continue reading. " * 3)
    out = await web.scrape_url({"url": "https://paywalled.example/story"})
    check("he says he couldn't read it", "couldn't read" in out, out[:120])
    check("...and nothing is citable from behind the wall",
          C.hosts() == set(), C.hosts())

    print("\n[3] a reply that names a page he never opened says so")
    C.start()
    C.record("https://www.reuters.com/markets/euro-cpi", "Euro area inflation", via="scrape_url")
    clean = "Inflation eased to 2.1 percent, sir, per https://reuters.com/markets/euro-cpi."
    check("a real source passes silently", C.caveat(clean) == "", C.caveat(clean))
    check("...even when the model shortened the path he read",
          C.unsupported("see https://reuters.com/") == [])
    made_up = "Bloomberg put it at 2.4 — https://bloomberg.com/news/eu-cpi."
    note = C.caveat(made_up)
    check("an invented source is caught", note != "", note)
    check("...named", "bloomberg.com" in note, note)
    check("...and owned as his, rather than dressed as a source",
          "that reference is mine" in note.lower() and "not a source" in note, note)
    check("a bare site NAME is not treated as a fabricated link",
          C.caveat("per Bloomberg this morning") == "")

    print("\n[4] the ledger belongs to one turn")
    C.start()
    check("a new turn starts with nothing vouched for", C.sources() == [], C.sources())
    check("...so yesterday's reading cannot support today's claim",
          C.unsupported("https://reuters.com/x") == ["reuters.com"])

    print("\n[5] the reply path actually applies it")
    from afon.brain.agent import _own_sources

    C.start()
    C.record("https://www.reuters.com/a", via="scrape_url")
    kept = _own_sources("Per https://reuters.com/a, yes sir.")
    check("a sourced reply is returned untouched", kept.endswith("yes sir."), kept)
    flagged = _own_sources("Per https://madeup.invalid/x, yes sir.")
    check("a fabricated one gets the clause appended", "didn't actually open" in flagged, flagged)
    check("...and the model's own words are left alone, not edited",
          flagged.startswith("Per https://madeup.invalid/x, yes sir."), flagged)
    check("a reply with no links is never touched",
          _own_sources("It's 14 degrees, sir.") == "It's 14 degrees, sir.")

    print("\n[6] provenance bookkeeping can never cost a turn")
    broken = "Per https://x.invalid/a."
    saved = C.unsupported
    try:
        C.unsupported = _boom
        check("a broken ledger returns the reply unchanged", _own_sources(broken) == broken)
    finally:
        C.unsupported = saved
    C.start()
    C.record("", via="x")
    C.record("not a url at all", via="x")
    check("junk in the ledger is ignored, not raised", True)

    print("\n[7] 41.F3 — a task states its budget and says when it stopped early")
    from afon.brain.worker import TaskWorker

    said: list[str] = []
    llm = _LoopLLM(["use a tool", "use a tool", "Here is what I found."])
    w = TaskWorker(llm, {"t": _noop_tool}, tools=[], max_steps=2)
    out = await w.run("research something big", on_progress=said.append)
    check("the budget is declared BEFORE the work, not in the postmortem",
          any("budget:" in s for s in said), said)
    check("...naming the step count", any("2 steps" in s for s in said), said)
    check("...and the wall clock", any("minute" in s for s in said), said)
    check("a task that ran out of steps says so", "all 2 steps" in out, out)
    check("...and offers to continue rather than just stopping", "keep going" in out, out)
    check("the reason is available as a value, not only as prose",
          w.stopped_early != "", w.stopped_early)

    llm2 = _LoopLLM(["Done in one."])
    w2 = TaskWorker(llm2, {"t": _noop_tool}, tools=[], max_steps=6)
    out2 = await w2.run("something small")
    check("a task that finished early claims no early stop", w2.stopped_early == "", w2.stopped_early)
    check("...and its result carries no apology", "as far as I got" not in out2, out2)

    # The clock, not just the step count: six steps of slow scraping is minutes of silence.
    slow = _LoopLLM(["use a tool"] * 8 + ["Partial."], delay=0.05)
    w3 = TaskWorker(slow, {"t": _noop_tool}, tools=[], max_steps=8, max_seconds=0.08)
    out3 = await w3.run("something slow")
    check("a task can run out of TIME as well as steps", "s of the" in w3.stopped_early,
          w3.stopped_early)
    check("...and the result says that too", "as far as I got" in out3, out3)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


async def _wrap(text):
    return text


def _boom(_text):
    raise RuntimeError("ledger on fire")


async def _noop_tool(_args):
    return "ok"


class _Msg:
    def __init__(self, content, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Call:
    def __init__(self):
        self.id = "c1"
        self.function = type("F", (), {"name": "t", "arguments": "{}"})()


class _LoopLLM:
    """Calls a tool for every scripted 'use a tool', then writes the last line."""

    def __init__(self, script, delay: float = 0.0):
        self._script = list(script)
        self._delay = delay

    async def complete(self, messages, tools=None, tool_choice=None):
        if self._delay:
            await asyncio.sleep(self._delay)
        line = self._script.pop(0) if self._script else "Done."
        if line == "use a tool":
            return _Msg("", [_Call()])
        return _Msg(line)


if __name__ == "__main__":
    asyncio.run(main())
