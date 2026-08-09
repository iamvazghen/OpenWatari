"""Memory retrieval upgrades — auto-RAG per turn (Fix #1) + un-frozen learned digest (Fix #2).

The store was well built but under-used: facts only reached the model via a startup-frozen digest or
if the model chose to call `recall`. These two fixes close that:
  * Fix #1: every substantive turn, memory relevant to what the owner just said is retrieved and
    injected as an ephemeral system note (keyword L1+L2, fast/local), so the model is grounded
    without having to call a tool.
  * Fix #2: the system-prompt learned-digest is rebuilt every N turns, so a fact learned mid-session
    surfaces without waiting for a brain restart.

Hermetic: a temp MemoryStore swapped in for the singleton, a capturing fake LLM. No network.

    uv run python bench/test_memory_autorecall.py
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


class CapturingLLM:
    """Records the messages of every completion; ends the turn with a plain reply (no tool calls)."""

    def __init__(self) -> None:
        self.seen: list[list[dict]] = []

    async def complete(self, messages, tools=None, temperature=0.6, tool_choice="auto", skip_primary=False):
        self.seen.append([dict(m) for m in messages])
        return SimpleNamespace(content="Noted, sir.", tool_calls=None)

    async def stream_with_tools(self, messages, tools=None, tool_choice="auto", skip_primary=False):
        self.seen.append([dict(m) for m in messages])
        yield "text", "Noted, sir."

    async def warmup(self):
        pass


def _fresh_store(tmp: str):
    import afon.brain.memory as mem
    store = mem.MemoryStore(base_dir=Path(tmp))
    mem.STORE = store          # _recall_note + context._learned_digest resolve the module global
    return store


async def main() -> None:
    from afon.config import settings
    settings.memory_enabled = True
    settings.memory_autorecall_enabled = True
    settings.memory_autorecall_min_words = 3

    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    store = _fresh_store(tmp.name)
    store.remember("the owner runs a rabbit farm in Armenia", tags=["project"])
    store.remember("the owner prefers replies under two sentences", tags=["preference"])
    store.journal_append("Discussed the rabbit farm irrigation plan.")

    from afon.brain.agent import AfonAgent
    agent = AfonAgent()

    print("[1] Fix #1 — _recall_note grounds a substantive turn, skips trivial ones")
    note = await agent._recall_note("how is the rabbit farm doing these days?")
    check("relevant fact retrieved for a real query", note is not None and "rabbit farm" in note, str(note))
    check("note is framed as background context, not a recital",
          note is not None and "don't recite" in note.lower(), str(note))
    check("journal (L2) hit can appear too", (await agent._recall_note("irrigation plan update")) is not None)
    check("bare affirmation is skipped", await agent._recall_note("yes") is None)
    check("too-short utterance is skipped", await agent._recall_note("hi there") is None)
    unrelated = await agent._recall_note("what's the capital of Japan right now")
    check("no memory hit -> no note (no noise)", unrelated is None, str(unrelated))
    settings.memory_autorecall_enabled = False
    check("respects the off-switch", await agent._recall_note("how is the rabbit farm") is None)
    settings.memory_autorecall_enabled = True

    print("\n[2] Fix #1 — respond() actually injects the note into the model's context")
    cap = CapturingLLM()
    agent._llm = cap
    await agent.respond("remind me about the rabbit farm situation please")
    injected = any("Relevant things you already know" in m.get("content", "")
                   for m in cap.seen[0] if m.get("role") == "system")
    check("auto-recall note is present in the turn's system context", injected, str([m['role'] for m in cap.seen[0]]))
    # And it must be EPHEMERAL — not persisted into history (or it would pollute future turns).
    check("the note is NOT stored in history",
          not any("Relevant things you already know" in (m.get("content") or "") for m in agent._history))

    print("\n[3] Fix #2 — the learned digest un-freezes (rebuilds every N turns)")
    settings.memory_digest_refresh_every_turns = 3
    agent2 = AfonAgent()
    base_prompt = agent2._system["content"]
    check("startup digest already contains an existing fact", "rabbit farm" in base_prompt, base_prompt[-200:])
    store.remember("the owner adopted a cat named Milo", tags=["personal"])
    check("a fact learned mid-session is NOT yet in the frozen prompt", "Milo" not in agent2._system["content"])
    for _ in range(3):
        agent2._maybe_refresh_digest()
    check("after the refresh interval the new fact IS in the prompt", "Milo" in agent2._system["content"],
          agent2._system["content"][-200:])

    try:
        tmp.cleanup()
    except Exception:  # noqa: BLE001
        pass

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
