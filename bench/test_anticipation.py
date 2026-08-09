"""Phase 2.3 — reasoned anticipation source (world-model -> LLM -> at most one Signal).

Asserts: it reasons over the world-model, emits the LLM's line as a Signal; honours a NONE verdict;
skips (no LLM call) when there's nothing to model; throttles via the interval gate; and is fail-quiet
when the LLM errors. Hermetic — fake LLM, injected world/clock, no network.

    uv run python bench/test_anticipation.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from types import SimpleNamespace  # noqa: E402

from afon.brain.anticipation import make_anticipation_source  # noqa: E402
from afon.brain.world_model import WorldModel  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class FakeLLM:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    async def complete(self, messages, tools=None, temperature=0.6, tool_choice="auto", skip_primary=False):
        self.calls += 1
        self.last_user = messages[-1]["content"]
        return SimpleNamespace(content=self.reply, tool_calls=None)


class BoomLLM:
    async def complete(self, *a, **k):
        raise RuntimeError("provider down")


async def _noop() -> None:
    """A no-op refresh so the hermetic tests don't reach out to live Notion."""
    return None


def _world(td: str):
    wm = WorldModel(Path(td) / "wm.json")
    wm.upsert_goal("migration", "Push Rently migrations 0007/0008", project="Rently",
                   deadline="2026-07-24T00:00:00+00:00")
    return wm


async def main() -> None:
    clock = {"t": 10_000.0}

    def now() -> float:
        return clock["t"]

    with tempfile.TemporaryDirectory() as td:
        print("[1] world-model present -> reasons, emits the LLM line as one Signal")
        llm = FakeLLM("Your Rently migration is unpushed and you fly Thursday, sir — want me to queue it?")
        src = make_anticipation_source(llm=llm, world=_world(td), recent=lambda: "", clock=now, refresh=_noop,
                                       min_interval_s=1800)
        sigs = await src()
        check("exactly one signal", len(sigs) == 1, str(len(sigs)))
        check("message is the LLM's line", sigs and "migration" in sigs[0].message.lower())
        check("kind tagged anticipation", sigs and sigs[0].kind == "anticipation")
        check("the world-model reached the prompt", "migrations 0007/0008" in llm.last_user)

        print("\n[2] interval gate: a second call within the window does NOT re-reason")
        calls_before = llm.calls
        sigs2 = await src()
        check("no signal inside the interval", sigs2 == [], repr(sigs2))
        check("LLM not called again", llm.calls == calls_before)
        clock["t"] += 1801  # advance past the gate
        sigs3 = await src()
        check("reasons again after the interval elapses", llm.calls == calls_before + 1 and len(sigs3) == 1)

        print("\n[3] NONE verdict -> no signal (interruption not earned)")
        llm_none = FakeLLM("NONE")
        src_none = make_anticipation_source(llm=llm_none, world=_world(td), recent=lambda: "",
                                            clock=now, refresh=_noop, min_interval_s=0)
        check("NONE yields no signal", await src_none() == [])
        check("but the LLM WAS consulted", llm_none.calls == 1)

        print("\n[4] nothing to model -> skip WITHOUT calling the LLM")
        empty = WorldModel(Path(td) / "empty.json")
        llm_unused = FakeLLM("should not be called")
        src_empty = make_anticipation_source(llm=llm_unused, world=empty, recent=lambda: "",
                                             clock=now, refresh=_noop, min_interval_s=0)
        check("no signal on an empty world", await src_empty() == [])
        check("LLM never called when there's nothing to reason about", llm_unused.calls == 0)

        print("\n[5] LLM error -> fail-quiet (no signal, no raise)")
        src_boom = make_anticipation_source(llm=BoomLLM(), world=_world(td), recent=lambda: "",
                                            clock=now, refresh=_noop, min_interval_s=0)
        try:
            check("a reasoning error yields no signal, no exception", await src_boom() == [])
        except Exception as e:  # noqa: BLE001
            check("source swallowed the error", False, f"{type(e).__name__}: {e}")

        print("\n[6] Phase 2.2 live-refresh: injected task refresh populates goals before reasoning")
        wm_live = WorldModel(Path(td) / "live.json")  # starts empty
        llm6 = FakeLLM("Your Q3 taxes are due in 3 days, sir — shall I block time?")

        async def refresh6() -> None:
            wm_live.refresh_from_tasks([{"id": "tax", "title": "File Q3 taxes", "due": "2026-07-23T00:00:00+00:00"}])

        src6 = make_anticipation_source(llm=llm6, world=wm_live, recent=lambda: "", clock=now,
                                        refresh=refresh6, min_interval_s=0)
        sigs6 = await src6()
        check("refresh pulled a task into the world-model", "tax" in [g.id for g in wm_live.active_goals()])
        check("reasoned over the freshly-refreshed goal", "File Q3 taxes" in llm6.last_user, llm6.last_user[:80])
        check("emitted the anticipation signal", len(sigs6) == 1 and sigs6[0].kind == "anticipation")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
