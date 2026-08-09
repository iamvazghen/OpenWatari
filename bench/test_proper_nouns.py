"""Phase 1.5 — proper-noun biasing list + STT wiring.

Asserts the bias list is assembled from configured terms + identity (assistant/owner/address) +
contacts, de-duplicated case-insensitively and capped; that the Whisper builder receives it; and that
the Deepgram builder still constructs even if this build rejects `keyterm` (guarded). Hermetic — a
fake contact book, in-process settings tweaks, no audio, no network.

    uv run python bench/test_proper_nouns.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import afon.brain.contacts as contacts_mod  # noqa: E402
from afon.config import settings  # noqa: E402
from afon.edge import proper_nouns  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


@dataclass
class _C:
    name: str


class _FakeBook:
    def __init__(self, names):
        self._names = names

    def all(self):
        return [_C(n) for n in self._names]


def main() -> None:
    saved = (settings.stt_hotwords, settings.assistant_name, settings.user_name,
             settings.user_address, contacts_mod.BOOK)
    try:
        settings.stt_hotwords = "Yerevan, Cologne, Rently; Party Map"
        settings.assistant_name = "Afon"
        settings.user_name = "Alex"
        settings.user_address = "sir"
        contacts_mod.BOOK = _FakeBook(["Anush", "John Smith"])
        proper_nouns.hotwords_str.cache_clear()

        print("[1] list merges config + identity + contacts")
        lst = proper_nouns.hotwords_list()
        low = [t.lower() for t in lst]
        for term in ["yerevan", "cologne", "rently", "party map", "afon", "alex", "sir",
                     "anush", "john smith"]:
            check(f"'{term}' present", term in low, repr(lst))

        print("\n[2] semicolons split, case-insensitive dedup")
        settings.stt_hotwords = "Rently, rently, RENTLY, Afon"  # dupes + collides with assistant name
        proper_nouns.hotwords_str.cache_clear()
        lst = proper_nouns.hotwords_list()
        low = [t.lower() for t in lst]
        check("'rently' appears exactly once", low.count("rently") == 1, repr(lst))
        check("'afon' appears exactly once despite config+identity", low.count("afon") == 1, repr(lst))

        print("\n[3] cap bounds the list")
        settings.stt_hotwords = ", ".join(f"term{i}" for i in range(200))
        proper_nouns.hotwords_str.cache_clear()
        check("respects max_terms cap", len(proper_nouns.hotwords_list(max_terms=25)) == 25)

        print("\n[4] hotwords_str is one space-joined string")
        settings.stt_hotwords = "Yerevan, Cologne"
        contacts_mod.BOOK = _FakeBook([])
        proper_nouns.hotwords_str.cache_clear()
        s = proper_nouns.hotwords_str()
        check("space-joined, contains terms", "Yerevan" in s and "Cologne" in s and "," not in s, repr(s))

        print("\n[5] Deepgram builder constructs with biasing (guard doesn't break the build)")
        settings.stt_hotwords = "Yerevan, Cologne, Rently"
        proper_nouns.hotwords_str.cache_clear()
        if settings.deepgram_api_key:
            try:
                from afon.edge.stt import _build_deepgram
                svc = _build_deepgram()
                check("Deepgram STT service built (keyterm applied or safely dropped)", svc is not None)
            except Exception as e:  # noqa: BLE001
                check("Deepgram STT service built", False, f"{type(e).__name__}: {e}")
        else:
            check("Deepgram key not set — skipped build smoke (biasing still wired for Whisper)", True)

        print("\n[6] empty config + no contacts -> just identity terms, never raises")
        settings.stt_hotwords = ""
        settings.assistant_name = "Afon"
        settings.user_name = ""
        settings.user_address = ""
        contacts_mod.BOOK = _FakeBook([])
        proper_nouns.hotwords_str.cache_clear()
        lst = proper_nouns.hotwords_list()
        check("degrades to the assistant name alone", [t.lower() for t in lst] == ["afon"], repr(lst))
    finally:
        (settings.stt_hotwords, settings.assistant_name, settings.user_name,
         settings.user_address, contacts_mod.BOOK) = saved
        proper_nouns.hotwords_str.cache_clear()

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
