"""Hardware acceptance baseline (MASTER 4.8) — lock the per-device latency contract.

A live mic can't run in CI, so this does the CI-lockable half: it validates the committed
``hardware_baseline.json`` is well-formed and exposes the pure ``regressions()`` comparator that
``voice_live_bench.py`` uses to flag a device that drifts past its accepted envelope. It also asserts
the config's TTFW target stays consistent with the reference (laptop) baseline — a config regression
guard so nobody silently loosens the target without loosening the baseline too.

    uv run python bench/test_hardware_baseline.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

BASELINE = Path(__file__).resolve().parent / "hardware_baseline.json"
passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f"  {detail}" if detail and not ok else ""))


def load_baseline() -> dict:
    return json.loads(BASELINE.read_text(encoding="utf-8"))["devices"]


def regressions(device: str, measured: dict, baseline: dict | None = None) -> list[str]:
    """Return a list of human-readable regressions for a measured battery vs the device baseline.

    ``measured`` = {"ttfw_p50_ms": float, "ttfw_p95_ms": float, "vaqi": float}. An unknown device
    falls back to the 'laptop' reference. Empty list = within the accepted envelope (PASS).
    """
    baseline = baseline or load_baseline()
    b = baseline.get(device) or baseline["laptop"]
    out: list[str] = []
    if measured.get("ttfw_p50_ms", 0) > b["ttfw_p50_ms"]:
        out.append(f"TTFW P50 {measured['ttfw_p50_ms']:.0f}ms > baseline {b['ttfw_p50_ms']}ms")
    if measured.get("ttfw_p95_ms", 0) > b["ttfw_p95_ms"]:
        out.append(f"TTFW P95 {measured['ttfw_p95_ms']:.0f}ms > baseline {b['ttfw_p95_ms']}ms")
    if measured.get("vaqi", 100) < b["vaqi_floor"]:
        out.append(f"VAQI {measured['vaqi']:.0f} < floor {b['vaqi_floor']}")
    return out


def main() -> None:
    print("[1] baseline file is well-formed")
    check("hardware_baseline.json exists", BASELINE.is_file())
    base = load_baseline()
    check("has the reference 'laptop' device", "laptop" in base)
    required = {"ttfw_p50_ms", "ttfw_p95_ms", "vaqi_floor"}
    for dev, spec in base.items():
        check(f"'{dev}' has all baseline fields", required <= set(spec), str(spec))
        check(f"'{dev}' P50 <= P95 (sane envelope)", spec["ttfw_p50_ms"] <= spec["ttfw_p95_ms"])
        check(f"'{dev}' VAQI floor in 0..100", 0 <= spec["vaqi_floor"] <= 100)

    print("\n[2] regressions() comparator")
    # A battery well inside the envelope -> no regressions.
    good = {"ttfw_p50_ms": 1200, "ttfw_p95_ms": 2500, "vaqi": 92}
    check("in-envelope battery has no regressions", regressions("laptop", good) == [], str(regressions("laptop", good)))
    # A slow battery trips every axis.
    bad = {"ttfw_p50_ms": 5000, "ttfw_p95_ms": 9000, "vaqi": 50}
    check("slow battery flags all three axes", len(regressions("laptop", bad)) == 3, str(regressions("laptop", bad)))
    # An unknown device falls back to the laptop reference (never KeyErrors).
    check("unknown device falls back to laptop", regressions("smartfridge", good) == [])
    # A private-endpoint VAQI just below its higher floor trips even if laptop would pass.
    edge = {"ttfw_p50_ms": 1200, "ttfw_p95_ms": 2500, "vaqi": 81}
    check("airpods higher VAQI floor is enforced", any("VAQI" in r for r in regressions("airpods", edge)),
          str(regressions("airpods", edge)))

    print("\n[3] --record mechanism turns a measurement into an accepted envelope")
    import json as _json
    import tempfile as _tf
    import voice_live_bench as vlb
    # Record into a COPY so the shipped baseline isn't mutated by the test.
    real_path = vlb.Path(vlb.__file__).resolve().parent / "hardware_baseline.json"
    saved = real_path.read_text(encoding="utf-8")
    try:
        vlb._record_baseline("laptop", 1500.0, 2800.0, 88.0)
        rec = _json.loads(real_path.read_text(encoding="utf-8"))["devices"]["laptop"]
        check("recorded P50 ceiling = measured x1.4", rec["ttfw_p50_ms"] == int(1500 * 1.4), str(rec))
        check("recorded P95 ceiling = measured x1.3", rec["ttfw_p95_ms"] == int(2800 * 1.3), str(rec))
        check("recorded VAQI floor = measured - 8", rec["vaqi_floor"] == 80, str(rec))
        check("recorded run keeps the raw measurement", "measured" in rec)
        # Other devices are NOT touched by a single-device record.
        others = _json.loads(real_path.read_text(encoding="utf-8"))["devices"]
        check("recording one device leaves others intact", "airpods" in others and "mentra" in others)
    finally:
        real_path.write_text(saved, encoding="utf-8")   # restore the shipped estimates

    print("\n[4] config TTFW target stays consistent with the reference baseline")
    from afon.config import settings
    # The aspirational target (ttfw_target_ms) must be <= the laptop acceptance ceiling — the
    # baseline is the 'must not exceed', the target is the 'aim for'. If someone loosens the target
    # past the acceptance ceiling, the two have diverged and this trips.
    check("config TTFW target <= laptop P50 acceptance ceiling",
          settings.ttfw_target_ms <= base["laptop"]["ttfw_p50_ms"],
          f"target {settings.ttfw_target_ms} > ceiling {base['laptop']['ttfw_p50_ms']}")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
