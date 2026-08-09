"""Vault search — the body cache must never serve stale text, and must not hold the loop (J3.3a).

The cache is the whole point and also the whole risk. `search_vault` read every .md in the vault
on every call: 10,670 files / 25.7MB, 4.9s of reads, on the event loop. Caching bodies by
(mtime, size) removes the reads — and introduces exactly one way to be wrong, which is serving
text that has since changed on disk. That is what the first three checks are for.

The fourth matters as much: the function was `async def` around a synchronous body, so awaiting it
froze the brain for the length of the scan. A coroutine that never yields also cannot be cut short
by `wait_for`, which is why the recall budget above it did nothing.

    uv run python bench/test_vault_search.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from afon.brain.tools import vault  # noqa: E402

_ok = 0
_fail = 0


def check(cond: bool, label: str, detail: str = "") -> None:
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"PASS  {label}" + (f"  [{detail}]" if detail else ""))
    else:
        _fail += 1
        print(f"FAIL  {label}" + (f"  [{detail}]" if detail else ""))


async def main() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        note = root / "charter.md"
        note.write_text("the owner runs a rabbit farm", encoding="utf-8")

        # Point the tools at the temp vault rather than the real one.
        saved = vault.settings.vault_path
        vault.settings.vault_path = str(root)
        try:
            r1 = await vault.search_vault({"query": "rabbit"})
            check("charter.md" in r1, "a fresh note is found", r1.splitlines()[0][:60])

            # THE STALENESS CASE. Rewrite the body and make the mtime unambiguously newer —
            # some filesystems have ~1s mtime granularity, so a fast test can rewrite a file
            # inside the same tick and the cache would legitimately see no change.
            note.write_text("the owner runs a vineyard", encoding="utf-8")
            os.utime(note, (time.time() + 2, time.time() + 2))

            r2 = await vault.search_vault({"query": "vineyard"})
            check("charter.md" in r2, "an edited note is re-read, not served from cache",
                  r2.splitlines()[0][:60])
            r3 = await vault.search_vault({"query": "rabbit"})
            check("Nothing in the vault matched" in r3,
                  "the OLD text is gone once the file changed", r3[:60])

            # A deleted file must not resurrect from the cache.
            note.unlink()
            r4 = await vault.search_vault({"query": "vineyard"})
            check("Nothing in the vault matched" in r4,
                  "a deleted note stops matching", r4[:60])

            # WARM-UP. Without it the L3 leg of fused_recall is not slow, it is UNREACHABLE: a cold
            # scan of the real vault costs ~108s against a 6s budget, so recall abandons the wait
            # every single time, and the module cache dies with the process so every restart is
            # cold again. Measured on the real vault 2026-08-09: 108s cold, 3.2s / 2.4s warm.
            (root / "alpha.md").write_text("the owner keeps bees", encoding="utf-8")
            (root / "beta.md").write_text("the owner keeps goats", encoding="utf-8")
            vault._CACHE.clear()
            vault._cached_bytes = 0
            n = await asyncio.to_thread(vault.warm_cache)
            check(n >= 2, "warm_cache reads every note", f"{n} notes")
            check(len(vault._CACHE) >= 2, "warm_cache populates the body cache",
                  f"{len(vault._CACHE)} entries")

            # The point of warming: the next search is served without re-reading anything.
            reads: list[Path] = []
            real_read = Path.read_text

            def _counting_read(self, *a, **kw):   # noqa: ANN001
                reads.append(self)
                return real_read(self, *a, **kw)

            Path.read_text = _counting_read       # noqa: B010
            try:
                r5 = await vault.search_vault({"query": "bees"})
            finally:
                Path.read_text = real_read        # noqa: B010
            check("alpha.md" in r5, "a warm search still finds the note", r5.splitlines()[0][:60])
            check(not reads, "a warm search re-reads NOTHING from disk",
                  f"{len(reads)} reads: {[p.name for p in reads][:3]}")

            # The loop must keep ticking while a search runs. Ticker beside the call, same
            # harness as bench/profile_memory_recall.py.
            (root / "big.md").write_text("rabbit " * 200_000, encoding="utf-8")
            gaps: list[float] = []

            async def _ticker(stop: asyncio.Event) -> None:
                last = time.perf_counter()
                while not stop.is_set():
                    await asyncio.sleep(0.01)
                    now = time.perf_counter()
                    gaps.append(now - last)
                    last = now

            stop = asyncio.Event()
            t = asyncio.create_task(_ticker(stop))
            await asyncio.sleep(0.05)
            await vault.search_vault({"query": "rabbit"})
            stop.set()
            await t
            worst = max(gaps[1:], default=0.0)
            check(worst < 0.500, "the event loop keeps ticking during a search",
                  f"worst gap {worst * 1000:.0f} ms")

            # An unconfigured vault is a clean answer, not a crash.
            vault.settings.vault_path = ""
            r5 = await vault.search_vault({"query": "rabbit"})
            check("vault" in r5.lower() and "Traceback" not in r5,
                  "no vault configured degrades to a clean message", r5[:60])
        finally:
            vault.settings.vault_path = saved
            vault._CACHE.clear()


asyncio.run(main())

print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
