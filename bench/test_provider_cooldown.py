"""Provider health is LEARNED, not configured — SYSTEMS.md 02.F3.

`llm.py` already benched a failing model for a fixed cooldown, and that is the configured kind of
health: one failure, one duration, applied to the single chain entry that happened to fail. It
learns nothing across entries. When a provider's key is rate-limited or its region is down, every
entry that provider serves is dead, and the chain rediscovers that one entry at a time — paying a
failed round-trip for each, on every turn, indefinitely. Nothing anywhere said so out loud, either:
`_unhealthy_until` was a private dict and the HUD showed the chain as fine.

So what has to be true:

  1. one failure is not evidence — a single blip must not move a provider;
  2. two inside five minutes is, and the provider is **deprioritised**;
  3. deprioritised means moved to the BACK of the chain, never removed. A provider that failed
     twice is not proven dead, and a chain that can empty itself is a chain that can leave Afon
     mute — the failure this whole file is downstream of;
  4. repeat offences cost more, and a success clears the record. Without the second half, the
     first bad five minutes of a day benches a provider until the next restart;
  5. a permanent failure (dead key, no credits) does not wait for a second strike;
  6. the HUD says so, for every provider — including the healthy ones, so a provider that never
     failed is a row saying "fine" rather than an absence nobody can read;
  7. one spelling of "which provider is this entry" — `_resolve` and `provider_of` disagreeing
     would bench a provider under a name nothing else uses, and the deprioritisation would
     silently stop applying (J3.6).

Run:
    uv run python bench/test_provider_cooldown.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import afon.brain.llm as L  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


class _Boom(Exception):
    pass


def main() -> int:
    print("[1] one spelling of which provider an entry belongs to")
    # `_resolve` maps an entry to a client; `provider_of` maps it to a health record. If those two
    # ever disagree, failures accumulate against a provider whose entries are never deprioritised.
    resolved = {name for name, *_ in L._PROVIDERS}
    check(f"the provider table is the only list of prefixes ({sorted(resolved)})",
          "groq:" not in Path(L.__file__).read_text(encoding="utf-8").split("_PROVIDERS")[2],
          "a hardcoded prefix survives outside the table")
    check("a prefixed entry names its provider",
          L.provider_of("groq:llama-3.3-70b") == "groq")
    check("a model name containing a colon does not confuse it",
          L.provider_of("ollama:llama3:8b") == "ollama",
          "splitting on the last colon would call this provider 'ollama:llama3'")
    check("an unprefixed entry belongs to the proxy",
          L.provider_of("gpt-4o-mini") == L._DEFAULT_PROVIDER)
    check("an unknown prefix is not silently invented as a provider",
          L.provider_of("weirdvendor:some-model") == L._DEFAULT_PROVIDER,
          "an invented provider name would collect failures nothing acts on")

    print("\n[2] one failure is a blip; two in five minutes is evidence")
    L.reset_provider_health()
    cooled = L.note_provider_failure("groq:a", why="429")
    check("a single failure does not deprioritise a provider", not cooled)
    check("...and nothing is cooling yet", not L.provider_cooling("groq:a"))
    cooled = L.note_provider_failure("groq:b", why="429")
    check("a SECOND failure inside the window does", cooled,
          "the two failures were on different entries of the same provider — that is the point")
    check("...and the whole provider is now cooling, not just the entry that failed",
          L.provider_cooling("groq:some-third-model"))
    check("a different provider is untouched", not L.provider_cooling("cerebras:x"))

    print("\n[3] the window is a window")
    L.reset_provider_health()
    old = time.monotonic() - (L._PROVIDER_FAIL_WINDOW_S + 30)
    L._PROVIDER_FAILS["groq"] = [old]
    cooled = L.note_provider_failure("groq:a", why="429")
    check("a failure older than the window does not count toward the threshold", not cooled,
          "two failures an hour apart is a working provider, not a failing one")

    print("\n[4] deprioritised means LAST, not gone")
    L.reset_provider_health()
    chain = ["groq:a", "cerebras:b", "groq:c", "minimax:d"]
    L.note_provider_failure("groq:a")
    L.note_provider_failure("groq:a")
    ordered = L.LLMClient._deprioritise(chain)
    check(f"every entry survives the reorder ({ordered})", sorted(ordered) == sorted(chain),
          "a chain that can empty itself can leave Afon mute")
    check("the cooling provider's entries are at the back",
          [e for e in ordered[-2:] if L.provider_of(e) == "groq"] == ["groq:a", "groq:c"],
          str(ordered))
    check("the healthy entries keep their configured order",
          [e for e in ordered if L.provider_of(e) != "groq"] == ["cerebras:b", "minimax:d"],
          "the chain order is a deliberate preference; only the bad provider may move")

    # ...and the chain builder actually applies it. Testing `_deprioritise` alone would pass
    # happily while `_candidate_chain` stopped calling it, which is the whole mechanism.
    import asyncio

    async def _chain_now(cooldown: float) -> list[str]:
        c = L.LLMClient.__new__(L.LLMClient)
        c._chain, c._unhealthy_until, c._cooldown = list(chain), {}, cooldown
        return L.LLMClient._candidate_chain(c)
    check("the chain the client actually builds is deprioritised",
          asyncio.run(_chain_now(45.0))[-2:] == ["groq:a", "groq:c"],
          str(asyncio.run(_chain_now(45.0))))
    check("...including when the per-model cooldown is switched off",
          asyncio.run(_chain_now(0.0))[-2:] == ["groq:a", "groq:c"],
          "the early return for cooldown=0 is its own path and skipped the reorder once")

    print("\n[5] repeat offences cost more, and recovery is learned")
    L.reset_provider_health()
    L.note_provider_failure("groq:a")
    L.note_provider_failure("groq:a")
    first = L._PROVIDER_COOL["groq"] - time.monotonic()
    L.note_provider_failure("groq:a")
    L.note_provider_failure("groq:a")
    second = L._PROVIDER_COOL["groq"] - time.monotonic()
    check(f"a second cooldown is longer than the first ({first:.0f}s -> {second:.0f}s)",
          second > first * 1.5)
    for _ in range(20):
        L.note_provider_failure("groq:a")
        L.note_provider_failure("groq:a")
    capped = L._PROVIDER_COOL["groq"] - time.monotonic()
    check(f"...but it is capped ({capped:.0f}s <= {L._PROVIDER_COOLDOWN_MAX_S:.0f}s)",
          capped <= L._PROVIDER_COOLDOWN_MAX_S + 1,
          "past the cap it is an outage, and the answer is paging, not a longer sulk")
    L.note_provider_success("groq:a")
    check("one success clears the cooldown", not L.provider_cooling("groq:a"),
          "without this, the first bad five minutes of the day benches a provider until restart")
    check("...but not the whole history — the strikes only step down",
          L._PROVIDER_STRIKES.get("groq", 0) > 0,
          "a provider that failed all morning is not pristine after one good answer")

    print("\n[6] a dead key does not get a second chance first")
    L.reset_provider_health()
    cooled = L.note_provider_failure("vercel:x", permanent=True, why="insufficient_quota")
    check("a permanent failure cools on the FIRST occurrence", cooled)
    check("...and for far longer than a transient one",
          (L._PROVIDER_COOL["vercel"] - time.monotonic()) > L._PROVIDER_COOLDOWN_MAX_S,
          "retrying a key with no credits is a round-trip that can never succeed")

    print("\n[7] the failure path actually calls it (not just the helper being correct)")
    L.reset_provider_health()
    client = L.LLMClient.__new__(L.LLMClient)
    client._unhealthy_until = {}
    client._cooldown = 0.0        # model-level bench OFF — the provider must still learn
    L.LLMClient._mark_failure(client, "groq:a", _Boom("boom"))
    L.LLMClient._mark_failure(client, "groq:a", _Boom("boom"))
    check("_mark_failure feeds the provider record", L.provider_cooling("groq:a"),
          "a provider must learn even where the per-model cooldown is switched off")
    client2 = L.LLMClient.__new__(L.LLMClient)
    client2._unhealthy_until = {}
    L.LLMClient._mark_success(client2, "groq:a", 0, "complete", 100.0, [])
    check("_mark_success clears it", not L.provider_cooling("groq:a"))

    print("\n[8] the HUD says so")
    L.reset_provider_health()
    L.note_provider_failure("groq:a", why="429 rate limited")
    L.note_provider_failure("groq:a", why="429 rate limited")
    from afon.brain.hud import hud_snapshot
    rows = hud_snapshot().get("llm_providers") or []
    by = {r["provider"]: r for r in rows}
    known = {name for name, *_ in L._PROVIDERS} | {L._DEFAULT_PROVIDER}
    check(f"every provider the chain can address has a row ({len(rows)})",
          set(by) == known, f"missing={sorted(known - set(by))}")
    check("a healthy provider is a row saying so, not an absence",
          by.get("cerebras", {}).get("cooling") is False,
          "an absence is a thing nobody notices; 31.F4 is the same lesson")
    check("the deprioritised provider is shown as such",
          by.get("groq", {}).get("cooling") is True, str(by.get("groq")))
    check("...with how long, and why",
          by["groq"]["cooling_for_s"] > 0 and "429" in by["groq"]["why"], str(by.get("groq")))
    L.reset_provider_health()

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
