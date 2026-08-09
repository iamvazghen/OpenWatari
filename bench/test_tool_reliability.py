"""Tools-util — Afon learns which of his own tools have been flaky from the audit trail.

Hermetic: seed a temp audit dir with real audit.record lines, then lock that reliability() aggregates
per-tool success rates, _flaky_tools flags only tools with enough calls AND a low rate, flaky_note is
silent when healthy / speaks when not and caches on a TTL, and reliability_summary ranks the worst first.

    uv run python bench/test_tool_reliability.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.config import settings  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    settings.audit_log_dir = str(tmp)   # point audit + reliability at a clean dir

    from afon.brain import audit
    from afon.brain import tool_reliability as tr

    print("[1] reliability() aggregates per-tool success from the audit trail")
    # a dependable tool: 6/6 ok. a flaky one: 2 ok / 4 fail. a rarely-used one: 1 fail (too few to judge).
    for _ in range(6):
        audit.record("send_email", {"to": "x"}, "sent", ok=True)
    for ok in (True, True, False, False, False, False):
        audit.record("browse_web", {"url": "u"}, "res" if ok else "timeout", ok=ok)
    audit.record("weird_tool", {}, "boom", ok=False)

    stats = tr.reliability()
    check("dependable tool at 100%", stats["send_email"]["rate"] == 1.0, str(stats.get("send_email")))
    check("flaky tool rate computed", stats["browse_web"]["rate"] == round(2 / 6, 2), str(stats.get("browse_web")))
    check("failure count tracked", stats["browse_web"]["failures"] == 4, str(stats.get("browse_web")))

    print("\n[2] _flaky_tools flags low-rate high-volume tools only")
    bad = dict(tr._flaky_tools(stats))
    check("flaky tool flagged", "browse_web" in bad)
    check("dependable tool NOT flagged", "send_email" not in bad)
    check("too-few-calls tool NOT flagged", "weird_tool" not in bad, str(bad))

    print("\n[3] flaky_note speaks when flaky, and caches on a TTL")
    tr._CACHE.update({"note": None, "at": 0.0})   # clear cache
    note = tr.flaky_note(now=1000.0)
    check("note names the flaky tool", note is not None and "browse_web" in note, str(note))
    check("note tells him to be honest about it", note is not None and "may not work" in note)
    # Within the TTL, a new failure shouldn't change the (cached) note.
    audit.record("send_email", {}, "boom", ok=False)
    check("cached within TTL", tr.flaky_note(now=1000.0 + tr._TTL_S - 1) is note)
    # Past the TTL it recomputes.
    recomputed = tr.flaky_note(now=1000.0 + tr._TTL_S + 1)
    check("recomputes past TTL", recomputed is not None)

    print("\n[4] flaky_note is silent when everything's healthy")
    tmp2 = Path(tempfile.mkdtemp())
    settings.audit_log_dir = str(tmp2)
    for _ in range(5):
        audit.record("good_tool", {}, "ok", ok=True)
    tr._CACHE.update({"note": None, "at": 0.0})
    check("healthy -> no note", tr.flaky_note(now=2000.0) is None)

    print("\n[5] reliability_summary ranks worst-first for the HUD")
    settings.audit_log_dir = str(tmp)
    summ = tr.reliability_summary()
    check("summary lists the flaky tool", any(r["tool"] == "browse_web" for r in summ), str(summ))
    check("summary carries call count + rate", summ and "calls" in summ[0] and "rate" in summ[0])

    print("\n[6] a window older than N days is excluded")
    tmp3 = Path(tempfile.mkdtemp())
    settings.audit_log_dir = str(tmp3)
    old = datetime.now(timezone.utc) - timedelta(days=30)
    (tmp3 / f"{old:%Y-%m-%d}.jsonl").write_text(
        '{"ts":"x","tool":"ancient","ok":false}\n' * 5, encoding="utf-8")
    check("30-day-old file excluded from 7-day window", "ancient" not in tr.reliability(days=7), str(tr.reliability(days=7)))
    check("but included in a 60-day window", "ancient" in tr.reliability(days=60))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
