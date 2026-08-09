"""Memory-util — Afon proactively resurfaces durable commitments, not just recalls them when asked.

Hermetic: seed a temp L1 store with commitment-flavoured and mundane facts, then lock that salient_notes()
ranks open commitments in the recency sweet spot above fresh/stale/mundane ones, and that the resurface
signal source raises the top one, records it, and rotates to the next rather than nagging the same memory.

    uv run python bench/test_memory_salience.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
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


def _seed(store, text: str, created: datetime, tags=None) -> None:
    """Write an L1 note with an explicit created timestamp (remember() always stamps 'now')."""
    store.learned_dir.mkdir(parents=True, exist_ok=True)
    from afon.brain.memory import _slug
    p = store.learned_dir / f"{created:%Y%m%d-%H%M%S}-{_slug(text)}.md"
    p.write_text(f"---\ncreated: {created.isoformat()}\ntags: {', '.join(tags or [])}\n---\n{text}\n",
                 encoding="utf-8")


def main() -> None:
    from afon.brain.memory import MemoryStore

    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    store = MemoryStore(base_dir=Path(tempfile.mkdtemp()))

    # A commitment in the sweet spot (8 days old), a fresh one (same commitment, today), a stale one
    # (60 days), a plain fact (no cue), and a goal-tagged one.
    _seed(store, "He wants to learn to play the piano.", now - timedelta(days=8))
    _seed(store, "He is planning to call his father this week.", now - timedelta(hours=3))
    _seed(store, "He was going to renew his passport.", now - timedelta(days=60))
    _seed(store, "His favourite colour is green.", now - timedelta(days=8))
    _seed(store, "Ship the Rently marketplace launch.", now - timedelta(days=6), tags=["goal"])

    print("[1] salient_notes ranks open commitments, drops plain facts")
    sal = store.salient_notes(now=now)
    texts = [s["text"] for s in sal]
    check("plain fact excluded", not any("favourite colour" in t for t in texts), str(texts))
    check("goal-tagged commitment included", any("Rently" in t for t in texts))
    check("sweet-spot commitment included", any("piano" in t for t in texts))
    check("ranked list is non-empty", len(sal) >= 3, str(len(sal)))

    print("\n[2] recency weighting: fresh & stale rank below the sweet spot")
    def score_of(sub):
        return next((s["score"] for s in sal if sub in s["text"]), None)
    check("8-day commitment beats today's", score_of("piano") > score_of("father"), str(sal))
    check("8-day commitment beats 60-day", score_of("piano") > score_of("passport"), str(sal))

    print("\n[3] resurface source raises the top one, then rotates")
    import afon.brain.memory as memory_mod
    import afon.brain.proactive_signals as ps

    saved_store = memory_mod.STORE
    saved_path = ps._RESURFACED_PATH
    try:
        memory_mod.STORE = store
        ps._RESURFACED_PATH = Path(tempfile.mkdtemp()) / "resurfaced.json"

        first = ps.memory_resurface_signals(now=now)
        check("a resurface signal is emitted", len(first) == 1 and first[0].kind == "resurface", str(first))
        check("it quotes a real commitment", first and any(
            k in first[0].message for k in ("piano", "Rently", "father", "passport")), str(first))
        top_text = first[0].message

        second = ps.memory_resurface_signals(now=now)
        check("does NOT repeat the same memory", second == [] or second[0].message != top_text,
              str(second))
        # Persisted the surfaced id.
        seen = json.loads(ps._RESURFACED_PATH.read_text(encoding="utf-8"))
        check("surfaced id persisted", len(seen) >= 1, str(seen))

        # Drain the rest; eventually silent once all salient memories have been raised once.
        for _ in range(10):
            ps.memory_resurface_signals(now=now)
        check("goes silent after all raised once", ps.memory_resurface_signals(now=now) == [])
    finally:
        memory_mod.STORE = saved_store
        ps._RESURFACED_PATH = saved_path

    print("\n[4] the source is registered on the live proactive tick")
    from afon.brain.proactive import default_signal_sources
    names = {getattr(s, "__name__", "") for s in default_signal_sources()}
    check("memory_resurface_signals registered", "memory_resurface_signals" in names, str(sorted(names)))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
