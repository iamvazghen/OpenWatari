"""Latency & quality benchmarks — TTFW and VAQI (Phase 5).

The implementation lives in the installable package (``afon.bench_metrics``) so the edge pipeline
(``edge/latency_meter.py``) can import it without depending on this test/dev ``bench`` dir. This
module just re-exports it for bench scripts/tests.
"""

from __future__ import annotations

from afon.bench_metrics import (  # noqa: F401
    TTFW,
    VAQI_WEIGHTS,
    Turn,
    format_report,
    vaqi,
)
