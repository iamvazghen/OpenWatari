"""Latency & quality benchmarks — TTFW and VAQI (Phase 5).

Two numbers tell you whether the voice loop *feels* good:

- **TTFW (Time-To-First-Word)** — milliseconds from the moment the user stops speaking to the
  first audible word of Afon's reply. The single most important "snappiness" metric. Target is
  ``AFON_TTFW_TARGET_MS`` (default 1200 ms on CPU).
- **VAQI (Voice Assistant Quality Index)** — one 0–100 score blending three things a transcript of
  latencies alone can't capture: how *fast* (latency vs target), how *reliable* (did every command
  get a response), and how *smooth* (no wrong interruptions / self-interruptions). Higher is better.

Everything here is pure computation so it's unit-testable and reusable both for a simulated battery
(``benchmark_battery.py``) and for live wiring (``edge/latency_meter.py`` feeds ``TTFW.record``).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass
class TTFW:
    """Accumulates Time-To-First-Word samples (milliseconds)."""

    samples_ms: list[float] = field(default_factory=list)

    def record(self, ms: float) -> None:
        if ms >= 0:
            self.samples_ms.append(float(ms))

    @property
    def count(self) -> int:
        return len(self.samples_ms)

    def summary(self) -> dict[str, float]:
        if not self.samples_ms:
            return {"count": 0, "mean": 0.0, "median": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0}
        s = sorted(self.samples_ms)
        p95 = s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]
        return {
            "count": len(s),
            "mean": statistics.fmean(s),
            "median": statistics.median(s),
            "p95": p95,
            "min": s[0],
            "max": s[-1],
        }


@dataclass
class Turn:
    """One utterance's outcome, fed to VAQI."""

    ttfw_ms: float | None = None        # None = no response was produced (a miss)
    false_interruption: bool = False    # Afon was wrongly cut off / interrupted himself
    responded: bool = True              # did Afon answer at all?


# VAQI component weights (sum to 1.0): speed, reliability, smoothness.
VAQI_WEIGHTS = {"latency": 0.4, "responsiveness": 0.35, "smoothness": 0.25}


def vaqi(turns: list[Turn], target_ms: float) -> dict[str, float]:
    """Compute the VAQI score (0–100) and its components from a battery of turns."""
    if not turns:
        return {"vaqi": 0.0, "latency": 0.0, "responsiveness": 0.0, "smoothness": 0.0, "turns": 0}

    n = len(turns)
    responded = [t for t in turns if t.responded and t.ttfw_ms is not None]
    missed_rate = 1.0 - (len(responded) / n)
    false_int_rate = sum(1 for t in turns if t.false_interruption) / n

    # Latency score: 1.0 when mean TTFW <= target, decaying as it overshoots. Only over responded
    # turns (a miss is penalised by responsiveness, not double-counted here).
    if responded:
        mean_ttfw = statistics.fmean(t.ttfw_ms for t in responded)
        latency_score = _clamp01(target_ms / mean_ttfw) if mean_ttfw > 0 else 1.0
    else:
        mean_ttfw, latency_score = 0.0, 0.0

    responsiveness = 1.0 - missed_rate
    smoothness = 1.0 - false_int_rate

    score = 100.0 * (
        VAQI_WEIGHTS["latency"] * latency_score
        + VAQI_WEIGHTS["responsiveness"] * responsiveness
        + VAQI_WEIGHTS["smoothness"] * smoothness
    )
    return {
        "vaqi": round(score, 1),
        "latency": round(latency_score, 3),
        "responsiveness": round(responsiveness, 3),
        "smoothness": round(smoothness, 3),
        "mean_ttfw_ms": round(mean_ttfw, 1),
        "missed_rate": round(missed_rate, 3),
        "false_interruption_rate": round(false_int_rate, 3),
        "turns": n,
    }


def format_report(ttfw: TTFW, turns: list[Turn], target_ms: float) -> str:
    s = ttfw.summary()
    q = vaqi(turns, target_ms)
    lines = [
        "Afon voice benchmarks",
        "-----------------------",
        f"TTFW (ms): mean {s['mean']:.0f} | median {s['median']:.0f} | p95 {s['p95']:.0f} "
        f"| min {s['min']:.0f} | max {s['max']:.0f}  (target {target_ms:.0f}, n={s['count']})",
        f"VAQI: {q['vaqi']}/100  "
        f"[latency {q['latency']} · responsiveness {q['responsiveness']} · smoothness {q['smoothness']}]",
        f"  missed {q.get('missed_rate', 0)} · false-interruptions {q.get('false_interruption_rate', 0)}"
        f" · turns {q['turns']}",
    ]
    return "\n".join(lines)
