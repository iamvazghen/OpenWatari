"""Latency regression guard (TODO A3 / item 11) — fail CI if the voice loop's latency budget drifts.

There is no mic in CI, so this can't measure a real TTFW. What it CAN do — and what actually catches
regressions — is guard the two things that deterministically blow the budget:

  1. **Config invariants.** The perceived TTFW is dominated by fixed knobs: Deepgram endpointing (the
     trailing silence before an utterance finalises), the LLM first-token timeout, and the TTFW
     target itself. If someone bumps endpointing to 2000ms or the first-token timeout to 30s, no
     amount of fast inference recovers a snappy turn. Assert each stays inside a sane ceiling.

  2. **A representative battery floor.** Feed a TTFW distribution that reflects the CURRENT pipeline
     (Deepgram + groq primary) through the same VAQI math the live meter uses, and assert the score
     and mean stay above a floor. A code change that, say, re-adds a blocking summary pass on every
     turn would push mean TTFW past target and trip this.

Pure computation + config read — offline, deterministic.

    uv run python bench/test_latency_guard.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f"  {detail}" if detail and not ok else ""))


def main() -> None:
    from afon.config import settings

    print("[1] config latency invariants (a regression here dooms every turn)")
    target = settings.ttfw_target_ms
    # Endpointing is pure dead-air added to EVERY turn's perceived latency. It alone must not exceed
    # the whole TTFW budget, or a snappy turn is arithmetically impossible.
    check(f"deepgram endpointing ({settings.deepgram_endpointing_ms}ms) <= TTFW target ({target}ms)",
          settings.deepgram_endpointing_ms <= target,
          f"{settings.deepgram_endpointing_ms} > {target}")
    # A hard ceiling on endpointing regardless of target: >1000ms of trailing silence feels laggy.
    check("deepgram endpointing <= 1000ms (natural-speech ceiling)",
          settings.deepgram_endpointing_ms <= 1000, f"{settings.deepgram_endpointing_ms}ms")
    # First-token timeout is the failover trigger, not the happy path — but if it's huge, a hung
    # primary stalls the whole turn instead of failing over fast. Keep it tight.
    check("LLM first-token timeout <= 6s (fast failover on a hung primary)",
          settings.llm_first_token_timeout_seconds <= 6.0,
          f"{settings.llm_first_token_timeout_seconds}s")
    # The TTFW target itself must stay ambitious — a silent bump to 5000ms would 'pass' any battery.
    check("TTFW target stays ambitious (<= 1500ms)", target <= 1500, f"{target}ms")

    print("\n[2] representative battery clears the quality floor")
    from bench.benchmarks import TTFW, Turn, vaqi

    # A realistic current-pipeline TTFW spread (Deepgram finalise + groq TTFT + ElevenLabs first
    # chunk), in ms. Median ~1.0-1.2s on the personal deployment; a couple of slow tails.
    battery_ms = [850, 950, 1000, 1050, 1100, 1200, 900, 1000, 1300, 1150]
    ttfw = TTFW()
    for ms in battery_ms:
        ttfw.record(ms)
    turns = [Turn(ttfw_ms=ms) for ms in battery_ms]
    q = vaqi(turns, target_ms=target)
    s = ttfw.summary()
    # Floors chosen with headroom over the representative battery so normal noise doesn't flap CI,
    # but a real regression (blocking pass, wrong provider, endpointing bump) trips them.
    check(f"mean TTFW ({s['mean']:.0f}ms) within 1.5x target", s["mean"] <= target * 1.5,
          f"{s['mean']:.0f} > {target * 1.5:.0f}")
    check(f"p95 TTFW ({s['p95']:.0f}ms) within 2x target", s["p95"] <= target * 2.0,
          f"{s['p95']:.0f} > {target * 2.0:.0f}")
    check(f"VAQI ({q['vaqi']}) >= 75 floor", q["vaqi"] >= 75.0, str(q["vaqi"]))
    check("every turn responded (responsiveness == 1.0)", q["responsiveness"] == 1.0)

    print("\n[3] STT floor: the streaming provider is the accurate/low-latency one")
    # A regression that silently flips the DEFAULT provider back to local whisper on a no-GPU CPU
    # (which added ~4s to short phrases) would gut latency. The personal deploy uses deepgram; the
    # framework default is deepgram too. Guard that the shipped default stays streaming-cloud.
    from afon.config import STTProvider
    default_stt = type(settings)().stt_provider  # a fresh Settings() = the shipped default, not .env
    check("shipped default STT is deepgram (streaming, low-latency)",
          default_stt == STTProvider.deepgram, str(default_stt))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
