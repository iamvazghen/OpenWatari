"""Live TTFW / VAQI battery — the scripted voice-quality benchmark (Roadmap 3.3).

The docs long said a 10-utterance live battery was "not built yet". This is it. It drives a real
brain through ``respond_stream`` for a fixed script of utterances (chat + data-read mix) and records,
per turn:

  * perceived first ack — when the deterministic pre-LLM acknowledgement fires (≈ immediate; this is
    what the user actually hears first, so it masks model TTFT)
  * TTFW — time to the first SPOKEN model sentence (the real first content word)
  * full turn — time to the last sentence
  * whether the turn produced any content at all (a "missed turn" if not)

then prints P50/P95 for TTFW and full-turn, the missed/false-interrupt counts, and a single VAQI
(Voice Assistant Quality Index, 0–100) so a run is one comparable number. Results are written to
``bench/_artifacts/voice_live_<ts>.json`` (gitignored).

It is INFORMATIONAL: it never fails the build, and if the brain LLM is unreachable it prints SKIP
and exits 0 (like efficiency_report.py). Run with the freellmapi tunnel / Groq key up:

    uv run python bench/voice_live_bench.py
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

GREEN, YELLOW, RED, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[0m"

# 10 scripted utterances: a realistic chat + data-read mix (data reads exercise the tool path,
# which is where TTFW is most at risk). Kept generic so the battery runs on any deployment.
SCRIPT: list[tuple[str, str]] = [
    ("chat", "Good morning."),
    ("chat", "How are you today?"),
    ("read", "What time is it?"),
    ("read", "What's the weather in Yerevan?"),
    ("read", "What's the price of bitcoin?"),
    ("read", "Convert 100 US dollars to euros."),
    ("chat", "Tell me one interesting fact."),
    ("read", "Define serendipity."),
    ("read", "What are the latest tech headlines?"),
    ("chat", "Thanks, that's all for now."),
]

TTFW_TARGET_MS = 1200    # measured first model content P50 goal (after tuning)
FULL_P95_TARGET_MS = 2500


async def _run_one(agent, text: str) -> dict:
    """Drive one utterance through the streaming path; return per-turn timings."""
    first_ack_ms: float | None = None
    ttfw_ms: float | None = None
    chars = 0
    t0 = time.perf_counter()

    def on_progress(note: str) -> None:
        nonlocal first_ack_ms
        if first_ack_ms is None:
            first_ack_ms = (time.perf_counter() - t0) * 1000

    try:
        async for sentence in agent.respond_stream(text, on_progress=on_progress):
            if ttfw_ms is None:
                ttfw_ms = (time.perf_counter() - t0) * 1000
            chars += len(sentence)
        full_ms = (time.perf_counter() - t0) * 1000
        return {"ack_ms": first_ack_ms, "ttfw_ms": ttfw_ms, "full_ms": full_ms,
                "chars": chars, "ok": chars > 0, "error": None}
    except Exception as e:  # noqa: BLE001 — a dead provider is a SKIP signal, not a crash
        return {"ack_ms": first_ack_ms, "ttfw_ms": None, "full_ms": None,
                "chars": 0, "ok": False, "error": type(e).__name__}


def _vaqi(ack_p50: float, chat_ttfw_p50: float, full_p95: float, missed: int, total: int) -> float:
    """A transparent 0–100 quality index built on what a voice user actually *feels*:

      * perceived responsiveness — when the first sound comes back (the deterministic ack). This is
        the dominant term: an elite assistant answers the instant you stop speaking.
      * chat first-word — for non-tool turns, how fast real content starts (tool turns legitimately
        wait on the tool's own network call, so they're judged on completion, not first word).
      * completion P95 and dropped turns.
    """
    score = 100.0
    score -= max(0.0, ack_p50 - 300) / 1000 * 25            # -25 per second of ack over 300ms
    score -= max(0.0, chat_ttfw_p50 - TTFW_TARGET_MS) / 1000 * 15
    score -= max(0.0, full_p95 - FULL_P95_TARGET_MS) / 1000 * 8
    score -= (missed / total) * 40 if total else 0          # dropped turns hurt most
    return max(0.0, min(100.0, score))


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) < 2:
        return values[0]
    return statistics.quantiles(values, n=100)[min(98, int(q) - 1)]


def _record_baseline(device: str, p50: float, p95: float, vaqi: float) -> None:
    """Write MEASURED numbers into hardware_baseline.json for `device`, with headroom.

    Turns the estimated envelope into a recorded one: ceilings = measured × 1.4 (P50) / × 1.3 (P95)
    so normal run-to-run variance doesn't flap the acceptance check, floor = measured VAQI − 8. Only
    the named device is updated; others keep their prior values. Records the raw measurement too.
    """
    bl_path = Path(__file__).resolve().parent / "hardware_baseline.json"
    data = json.loads(bl_path.read_text(encoding="utf-8"))
    prev = data["devices"].get(device, {})
    data["devices"][device] = {
        "ttfw_p50_ms": int(p50 * 1.4),
        "ttfw_p95_ms": int(p95 * 1.3),
        "vaqi_floor": max(0, int(vaqi - 8)),
        "note": prev.get("note", ""),
        "measured": {"ttfw_p50_ms": round(p50, 1), "ttfw_p95_ms": round(p95, 1),
                     "vaqi": round(vaqi, 1), "recorded_at": time.strftime("%Y-%m-%d %H:%M")},
    }
    bl_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"\n  RECORDED baseline for '{device}': P50<={int(p50*1.4)}ms P95<={int(p95*1.3)}ms "
          f"VAQI>={max(0, int(vaqi-8))} (from measured {p50:.0f}/{p95:.0f}/{vaqi:.0f})")


async def main(device: str = "laptop", record: bool = False) -> None:
    from afon.brain.agent import AfonAgent

    print("Live TTFW / VAQI battery — needs the brain LLM reachable (freellmapi tunnel / Groq key).\n")
    agent = AfonAgent()
    try:
        await agent.warmup()
    except Exception:  # noqa: BLE001
        pass

    rows: list[dict] = []
    for kind, text in SCRIPT:
        r = await _run_one(agent, text)
        r.update({"kind": kind, "text": text})
        rows.append(r)
        agent.reset_session(reason="bench")  # isolate each utterance's timing
        mark = "ok " if r["ok"] else "ERR"
        ttfw = f"{r['ttfw_ms']:.0f}ms" if r["ttfw_ms"] is not None else "-"
        full = f"{r['full_ms']:.0f}ms" if r["full_ms"] is not None else "-"
        print(f"  [{mark}] {kind:4} TTFW={ttfw:>8}  full={full:>8}  {text}")

    ok_rows = [r for r in rows if r["ok"]]
    if not ok_rows:
        errs = {r["error"] for r in rows if r["error"]}
        print(f"\nSKIP — the brain LLM was unreachable ({', '.join(sorted(errs)) or 'no content'}). "
              "Start the freellmapi tunnel / set the Groq key and re-run.")
        return

    fulls = [r["full_ms"] for r in ok_rows]
    chat_ttfws = [r["ttfw_ms"] for r in ok_rows if r["kind"] == "chat" and r["ttfw_ms"] is not None]
    tool_fulls = [r["full_ms"] for r in ok_rows if r["kind"] == "read"]
    acks = [r["ack_ms"] for r in rows if r["ack_ms"] is not None]
    missed = sum(1 for r in rows if not r["ok"])
    ack_p50 = statistics.median(acks) if acks else 0.0
    chat_ttfw_p50 = statistics.median(chat_ttfws) if chat_ttfws else 0.0
    p50_full, p95_full = statistics.median(fulls), _pct(fulls, 95)
    vaqi = _vaqi(ack_p50, chat_ttfw_p50, p95_full, missed, len(rows))

    def grade(v: float, good: float) -> str:
        c = GREEN if v <= good else (YELLOW if v <= good * 1.25 else RED)
        return f"{c}{v:.0f} ms{RESET}"

    print("\n  --- results ---")
    print(f"  perceived first ack   P50 {grade(ack_p50, 300)}   (what you actually hear first)")
    print(f"  chat first word       P50 {grade(chat_ttfw_p50, TTFW_TARGET_MS)}   target ≤ {TTFW_TARGET_MS} ms")
    if tool_fulls:
        print(f"  tool turn full        P50 {grade(statistics.median(tool_fulls), 2500)}   "
              "(includes the tool's own network call)")
    print(f"  full turn             P50 {grade(p50_full, 1800)}   P95 {grade(p95_full, FULL_P95_TARGET_MS)}   target P95 ≤ {FULL_P95_TARGET_MS} ms")
    print(f"  missed turns          {missed}/{len(rows)}")
    vc = GREEN if vaqi >= 85 else (YELLOW if vaqi >= 70 else RED)
    print(f"  VAQI                  {vc}{vaqi:.1f}/100{RESET}")

    # Acceptance check against the committed per-device baseline (MASTER 4.8). The battery here uses
    # the chat first-word P50 as the perceived-TTFW proxy and full P95 as the tail. A drift past the
    # device envelope prints REGRESSION (informational — this bench never fails the build).
    regs: list[str] = []
    try:
        from test_hardware_baseline import regressions
        measured = {"ttfw_p50_ms": chat_ttfw_p50 or p50_full, "ttfw_p95_ms": p95_full, "vaqi": vaqi}
        regs = regressions(device, measured)
        print(f"\n  --- acceptance vs baseline [{device}] ---")
        if regs:
            print(f"  {RED}REGRESSION{RESET} on {device}:")
            for r in regs:
                print(f"    - {r}")
        else:
            print(f"  {GREEN}within the accepted envelope for {device}{RESET}")
    except Exception as e:  # noqa: BLE001 — baseline check is best-effort
        print(f"  (baseline check skipped: {type(e).__name__})")

    out_dir = Path(__file__).resolve().parent / "_artifacts"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"voice_live_{time.strftime('%Y%m%d-%H%M%S')}.json"
    out_path.write_text(json.dumps({
        "ts": time.time(),
        "ttfw_target_ms": TTFW_TARGET_MS,
        "full_p95_target_ms": FULL_P95_TARGET_MS,
        "perceived_ack_p50_ms": ack_p50 if acks else None,
        "chat_first_word_p50_ms": chat_ttfw_p50 if chat_ttfws else None,
        "p50_full_ms": p50_full, "p95_full_ms": p95_full,
        "missed_turns": missed, "total": len(rows), "vaqi": vaqi,
        "device": device, "baseline_regressions": regs,
        "rows": rows,
    }, indent=2), encoding="utf-8")
    print(f"\n  wrote {out_path}")

    # --record: persist THIS run's measured numbers as the device's accepted baseline.
    if record:
        _record_baseline(device, chat_ttfw_p50 or p50_full, p95_full, vaqi)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Live TTFW/VAQI battery with per-device acceptance check")
    ap.add_argument("--device", default="laptop",
                    help="device kind for the baseline check: laptop | airpods | mentra | iphone | android")
    ap.add_argument("--record", action="store_true",
                    help="write THIS run's measured numbers into hardware_baseline.json for --device")
    args = ap.parse_args()
    asyncio.run(main(device=args.device, record=args.record))
