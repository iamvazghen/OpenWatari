"""Lightweight process metrics — structured observability without a heavy stack (TODO 8.8).

A single-user voice assistant doesn't need Prometheus + Grafana. This is a tiny in-process registry
of counters + rolling latency summaries that the brain exposes as JSON at ``GET /metrics`` (auth-
gated). It answers the questions that actually matter for THIS system: how many turns has it served,
how many tool calls / errors, how often the LLM chain fails over, what the recent TTFW looks like,
and how long it's been up.

Zero dependencies, thread-safe (a lock around dict mutations, since the HTTP handler reads from a
different thread than the async agent writes). ``snapshot()`` is a plain dict → ``json.dumps``.

ponytail: in-memory only. Metrics reset on restart — that's fine for a single always-on process; if
you ever need history, scrape /metrics into a time-series DB externally rather than growing this.
"""

from __future__ import annotations

import threading
import time
from collections import deque


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._latencies: dict[str, deque[float]] = {}   # name -> recent samples (ms)
        self._last: dict[str, float] = {}               # name -> last observed value
        self._started = time.time()

    def incr(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + n

    def observe(self, name: str, value: float) -> None:
        """Record a latency/gauge sample (keeps the last 200 for a rolling summary)."""
        with self._lock:
            self._last[name] = value
            dq = self._latencies.get(name)
            if dq is None:
                dq = self._latencies[name] = deque(maxlen=200)
            dq.append(float(value))

    @staticmethod
    def _summary(samples: list[float]) -> dict[str, float]:
        if not samples:
            return {"count": 0, "p50": 0.0, "p95": 0.0, "mean": 0.0}
        s = sorted(samples)
        p = lambda q: s[min(len(s) - 1, int(round(q * (len(s) - 1))))]  # noqa: E731
        return {"count": len(s), "p50": round(p(0.5), 1), "p95": round(p(0.95), 1),
                "mean": round(sum(s) / len(s), 1)}

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "uptime_s": round(time.time() - self._started, 1),
                "counters": dict(self._counters),
                "latency_ms": {k: self._summary(list(v)) for k, v in self._latencies.items()},
                "last": dict(self._last),
            }


# Process-wide singleton. Import and use directly: `from afon.brain.metrics import METRICS`.
METRICS = Metrics()
