"""Phase 1.2 — the AEC seam: open-speaker full-duplex when a real echo-canceller is active.

The working canceller (Krisp/ai-coustics) is a proprietary SDK we can't bundle, so this locks the parts
that ARE ours: the filter builder degrades cleanly when the SDK isn't installed (never crashes the edge),
noise-only names are refused, and — the actual integration — ``resolve_barge_in`` flips OPEN speakers to
full-duplex exactly when an echo-canceller is on the mic, while leaving every other case unchanged.

    uv run python bench/test_aec.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.edge.aec import aec_active, build_input_filter  # noqa: E402
from afon.edge.device_profile import OutputKind, resolve_barge_in  # noqa: E402

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
    print("[1] the filter builder degrades cleanly (never crashes the edge)")
    check("none -> no filter", build_input_filter("none") is None)
    check("blank/None -> no filter", build_input_filter(None) is None)
    check("unknown name -> no filter", build_input_filter("wobble") is None)
    check("rnnoise refused (noise != echo, would self-interrupt)", build_input_filter("rnnoise") is None)
    # krisp/aic SDKs aren't installed here, so these must degrade to None WITHOUT raising.
    krisp = build_input_filter("krisp")
    check("krisp without its SDK -> None, no raise", krisp is None or aec_active(krisp))
    check("aec_active(None) is False", aec_active(None) is False)

    class _FakeFilter:
        pass

    check("aec_active(a real filter) is True", aec_active(_FakeFilter()) is True)

    print("\n[2] the integration: AEC active makes OPEN speakers full-duplex")
    on, kind, why = resolve_barge_in("auto", output_name="Realtek Speakers", aec_active=True)
    check("speakers + AEC -> barge-in ON", on is True, why)
    check("still classified as the speaker endpoint", kind == OutputKind.speakers)
    check("reason names AEC", "AEC" in why, why)

    print("\n[3] without AEC, open speakers stay half-duplex (unchanged, speaker-safe)")
    on2, _, why2 = resolve_barge_in("auto", output_name="Realtek Speakers", aec_active=False)
    check("speakers, no AEC -> barge-in OFF", on2 is False, why2)
    check("default (aec omitted) is also OFF", resolve_barge_in("auto", output_name="Laptop Speaker")[0] is False)

    print("\n[4] AEC never overrides the other decisions")
    check("private endpoint stays ON regardless of AEC",
          resolve_barge_in("auto", device_hint="airpods", aec_active=False)[0] is True)
    check("forced off wins even with AEC",
          resolve_barge_in("off", output_name="Speakers", aec_active=True)[0] is False)
    check("forced on wins",
          resolve_barge_in("on", output_name="Speakers", aec_active=False)[0] is True)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
