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

    provenance()

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


# ── 30.F3: every stored fact carries source, timestamp and confidence ────────────────────────
# The store mixes three different kinds of claim — what the owner said, what a model inferred from
# a conversation, and what the pattern detector guessed from behaviour — and until now they were
# indistinguishable once written. That is how a guess quietly becomes something Afon "knows", and
# it is not recoverable after the fact: a note with no origin cannot be given one later.
def provenance() -> None:
    import re

    from afon.brain.memory import DEFAULT_SOURCE, SOURCES, MemoryStore, source_rank

    print("\n[5] a fact written today carries all three")
    store = MemoryStore(base_dir=Path(tempfile.mkdtemp()))
    store.remember("He drinks oat milk.", source="owner")
    note = next(iter(store._iter_notes()))
    check("source is stored", note.source == "owner", note.source)
    check("confidence is stored", note.confidence == SOURCES["owner"], str(note.confidence))
    check("timestamp is stored", bool(note.created) and note.created[:2] == "20", note.created)
    raw = note.path.read_text(encoding="utf-8")
    check("...and all three survive a round-trip through the file, not just the object",
          "source: owner" in raw and "confidence:" in raw and "created:" in raw, raw[:120])

    print("\n[6] the vocabulary is closed")
    check("every declared source has a confidence", all(0.0 <= v <= 1.0 for v in SOURCES.values()))
    check("the owner outranks everything",
          all(source_rank("owner") >= source_rank(s) for s in SOURCES))
    store.remember("He has a rabbit farm.", source="astrology")
    bad = [n for n in store._iter_notes() if "rabbit" in n.text][0]
    check("an unknown source is not invented, it falls back to the default",
          bad.source == DEFAULT_SOURCE, bad.source)

    print("\n[7] EVERY fact reports all three — including the ones written before this existed")
    # The interesting case. A note with no frontmatter at all has no origin and no timestamp, and
    # the honest answer is to name that rather than backfill a confidence nobody measured.
    legacy = store.learned_dir / "20250101-120000-old-note.md"
    legacy.write_text("He used to live in Yerevan.\n", encoding="utf-8")
    half = store.learned_dir / "20250102-120000-half-note.md"
    half.write_text("---\ncreated: 2025-01-02T12:00:00+00:00\ntags: \n---\nHe likes espresso.\n",
                    encoding="utf-8")
    notes = list(store._iter_notes())
    check(f"all {len(notes)} notes carry a source", all(n.source in SOURCES for n in notes),
          str([(n.text[:20], n.source) for n in notes if n.source not in SOURCES]))
    check("all notes carry a confidence in range",
          all(isinstance(n.confidence, float) and 0.0 <= n.confidence <= 1.0 for n in notes))
    check("all notes carry a timestamp",
          all(n.created for n in notes),
          str([n.text[:20] for n in notes if not n.created]))
    old = [n for n in notes if "Yerevan" in n.text][0]
    check("a pre-provenance note is named 'legacy', not silently attributed",
          old.source == "legacy", old.source)
    check("...and its timestamp falls back to the file's own mtime rather than staying blank",
          old.created.startswith("20"), old.created)
    partial = [n for n in notes if "espresso" in n.text][0]
    check("a note with frontmatter but no source is legacy too", partial.source == "legacy",
          partial.source)

    print("\n[8] a better source upgrades what was already known")
    # The moment a guess becomes a fact: the owner states outright something Afon had inferred.
    # Dedup returns early on identical text, so without this the stronger provenance is discarded.
    store2 = MemoryStore(base_dir=Path(tempfile.mkdtemp()))
    store2.remember("He is learning German.", source="inferred")
    store2.remember("He is learning German.", source="owner")
    notes = list(store2._iter_notes())
    check("the fact is still stored once", len(notes) == 1, str([n.text for n in notes]))
    check("...but now attributed to the owner", notes[0].source == "owner", notes[0].source)
    check("...with the owner's confidence", notes[0].confidence == SOURCES["owner"])
    check("...and its original timestamp is kept",
          notes[0].created.startswith("20") and "created:" in
          notes[0].path.read_text(encoding="utf-8"))
    store2.remember("He is learning German.", source="pattern")
    notes = list(store2._iter_notes())
    check("a WEAKER source does not downgrade a fact the owner stated",
          notes[0].source == "owner", notes[0].source)

    print("\n[9] no caller can write a fact without saying where it came from")
    # The structural half. A default parameter means a new call site silently attributes its facts
    # to whatever the default happens to be, and nothing would ever fail — the same shape as
    # 36.F5's "no credential-named setting may carry a default".
    root = Path(__file__).resolve().parents[1] / "src"
    callers = []
    for p in root.rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"\.remember\(", text):
            call = text[m.start():m.start() + 400]
            if "def remember" in call:
                continue
            depth, end = 0, 0
            for i, ch in enumerate(call):
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            if "source=" not in call[:end + 1]:
                callers.append(f"{p.relative_to(root)}:{text[:m.start()].count(chr(10)) + 1}")
    check(f"every .remember() call site in src/ names its source ({len(callers)} without)",
          not callers, str(callers))


if __name__ == "__main__":
    main()
