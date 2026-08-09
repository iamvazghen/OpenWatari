"""Self-improvement loop (Hermes-style background_review) — learns durable facts into L1 memory.

Hermetic: fake LLM, temp memory dir (never touches the real memory/). Verifies extraction, the
dedup invariant, the too-short-conversation guard, and graceful failure.

    uv run python bench/test_self_improve.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

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


class FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def complete(self, messages, tools=None, temperature=0.6, tool_choice="auto", skip_primary=False):
        self.calls += 1
        return SimpleNamespace(content=self.content, tool_calls=None)


CONVO = [
    {"role": "user", "content": "From now on keep your replies under two sentences, and call yourself Afon."},
    {"role": "assistant", "content": "Understood, sir."},
    {"role": "user", "content": "My rabbit farm is in Armavir and I train at 6am."},
    {"role": "assistant", "content": "Noted, sir."},
]


async def main() -> None:
    from afon.brain.background_review import _parse_facts, review_and_learn
    from afon.brain.memory import MemoryStore

    print("[1] facts are extracted from a conversation and written to L1")
    with tempfile.TemporaryDirectory() as d:
        store = MemoryStore(base_dir=d)
        llm = FakeLLM('["Vazghen wants replies under two sentences.", '
                      '"Vazghen renamed his assistant to Afon.", '
                      '"Vazghen has a rabbit farm in Armavir."]')
        learned = await review_and_learn(CONVO, llm, store=store)
        check("the reviewer called the model once", llm.calls == 1, str(llm.calls))
        check("three facts learned into L1", store.count() == 3, str(store.count()))
        check("returned the learned facts", len(learned) == 3, str(learned))

        print("\n[2] dedup invariant — re-running learns nothing new")
        await review_and_learn(CONVO, llm, store=store)
        check("still three facts (no duplicates)", store.count() == 3, str(store.count()))

        print("\n[3] the facts are recallable")
        hits = store.recall("rabbit farm", limit=3)
        check("recall finds the Armavir fact", any("Armavir" in h for h in hits), str(hits))

    print("\n[4] guards: too-short convo and bad JSON learn nothing, never raise")
    with tempfile.TemporaryDirectory() as d:
        store = MemoryStore(base_dir=d)
        short = await review_and_learn([{"role": "user", "content": "hi"}], FakeLLM("[]"), store=store)
        check("too-short conversation is skipped", short == [] and store.count() == 0)
        check("non-JSON model output -> no facts, no crash",
              _parse_facts("I think maybe nothing to save here.") == [])

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
