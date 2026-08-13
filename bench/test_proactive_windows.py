"""No proactive kind may speak outside its purpose window — asserted for EVERY kind, every hour.

The per-feature window tests (`test_coaching.py`, `test_interventions.py`) each check their own
feature. Nothing checked the guarantee ACROSS kinds, so a new signal kind — and kinds get added
often — inherited no timing guarantee at all. Adding one is a one-line `Signal(..., kind="x")` in a
generator, and nothing would have noticed it waking the owner at 03:00.

What is asserted is the invariant the code actually has, not one invented here: a routine-grade
signal (urgency < quiet_override) cannot be spoken during quiet hours, whatever its kind, and an
emergency-grade one still can. `KINDS` is derived by grepping the source for `kind=` so a kind added
later is swept automatically instead of silently escaping the sweep — a hard-coded list would rot
into exactly the gap this file exists to close.

Hermetic: injected clock, injected emit, no scheduler, no network.

    uv run python bench/test_proactive_windows.py
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

passed = failed = 0

QUIET = "23:00-07:00"          # matches the shipped default shape: wraps past midnight
OVERRIDE = 0.95                # urgency at/above which a signal may break quiet hours


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def discover_kinds() -> list[str]:
    """Every kind the source actually constructs, plus the dataclass default."""
    found = {"note"}   # Signal.kind's default — a generator that omits kind still produces one
    for py in (ROOT / "src" / "afon" / "brain").rglob("*.py"):
        # `[a-z_-]`, not `[a-z_]`: kinds like "calendar-prep" are hyphenated, and the narrower
        # pattern dropped them from the sweep silently — a discovery bug that looks identical to
        # "that kind doesn't exist", which is the whole failure mode this file guards against.
        for m in re.finditer(r'kind\s*=\s*"([a-z_-]+)"', py.read_text(encoding="utf-8")):
            found.add(m.group(1))
    return sorted(found)


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def main() -> None:
    import asyncio

    from afon.brain.proactive import ProactiveEngine, Signal, in_quiet_hours

    kinds = discover_kinds()
    print(f"[1] kinds discovered from source: {kinds}")
    check("the sweep found a plausible set of kinds (not an empty/stale list)",
          len(kinds) >= 5, str(kinds))

    def run_at(hour: int, kind: str, urgency: float) -> list:
        sent: list = []

        async def emit(message, u, speak):
            sent.append((message, u, "voice" if speak else "push"))
            return "voice" if speak else "push"

        clock = Clock(datetime(2026, 8, 11, hour, 30))
        eng = ProactiveEngine(
            emit=emit,
            sources=[lambda: [Signal("k", f"{kind} note", urgency, kind=kind)]],
            is_listening=lambda: True,
            clock=clock,
            quiet_hours=QUIET,
            daily_budget=99,
            threshold=0.6,
            repeat_suppress_minutes=0,
            quiet_override=OVERRIDE,
        )
        asyncio.run(eng.maybe_interject())
        return sent

    print("\n[2] routine-grade signals: SILENT in quiet hours, for every kind, every hour")
    leaked: list[str] = []
    silent_when_awake: list[str] = []
    for kind in kinds:
        for hour in range(24):
            quiet = in_quiet_hours(datetime(2026, 8, 11, hour, 30), QUIET)
            sent = run_at(hour, kind, urgency=0.80)   # above threshold, below override
            if quiet and sent:
                leaked.append(f"{kind}@{hour:02d}:30")
            if not quiet and not sent:
                silent_when_awake.append(f"{kind}@{hour:02d}:30")
    check(f"no kind speaks a routine signal during quiet hours ({len(kinds)} kinds x 24h)",
          not leaked, f"leaked: {leaked[:8]}")
    # The other half. Without this, a gate that blocks EVERYTHING would pass the check above --
    # which is the failure mode that makes a one-sided timing test worse than none.
    check("...and every kind still speaks outside quiet hours",
          not silent_when_awake, f"wrongly silent: {silent_when_awake[:8]}")

    print("\n[3] emergency-grade signals DO break quiet hours (the override is real)")
    broke = [k for k in kinds if run_at(3, k, urgency=0.99)]
    check("an urgency >= override reaches the owner at 03:30 for every kind",
          len(broke) == len(kinds), f"blocked: {sorted(set(kinds) - set(broke))}")

    print("\n[4] the boundary minutes, where an off-by-one would hide")
    edge = [
        (22, 59, False), (23, 0, True), (23, 1, True),
        (6, 59, True), (7, 0, False), (7, 1, False),
    ]
    for h, m, want_quiet in edge:
        got = in_quiet_hours(datetime(2026, 8, 11, h, m), QUIET)
        check(f"{h:02d}:{m:02d} quiet={want_quiet}", got == want_quiet, f"got {got}")

    print("\n[5] a wrapping window is handled as a window, not as start<end")
    # 23:00-07:00 wraps midnight. A naive `start <= x < end` reads that as an EMPTY window and
    # would make every check above pass vacuously by never being quiet at all.
    quiet_hours_count = sum(in_quiet_hours(datetime(2026, 8, 11, h, 30), QUIET) for h in range(24))
    check("the wrapping quiet window covers 8 hours, not 0", quiet_hours_count == 8,
          f"covered {quiet_hours_count}h")

    print("\n[6] signal keys must be STABLE across ticks, or suppression cannot work")
    # The engine suppresses repeats via `self._spoken_at[signal.key]`. A key built from the CURRENT
    # TIME is different on every tick, so it can never match — and the source re-nudges about the
    # same thing until the daily budget runs out. `anticipatory_prep` did exactly that
    # (`f"anticipatory-{now.isoformat()}"`): a meeting 30 min out produced a fresh "starting soon"
    # signal on all 6 ticks before it. A source-wide grep is the cheap guard, because the defect is
    # invisible in any single-tick test — every one of those signals looks perfectly correct alone.
    offenders: list[str] = []
    key_re = re.compile(r'key\s*=\s*f?"[^"]*\{[^}]*\b(now|_utc_now\(\)|datetime\.now\(\)?[^}]*)'
                        r'[^}]*\}[^"]*"')
    for py in (ROOT / "src" / "afon" / "brain").rglob("*.py"):
        for m in key_re.finditer(py.read_text(encoding="utf-8")):
            # A DATE stamp is legitimate — it scopes a signal to "once today" (news, digests).
            # A full timestamp is not: it scopes it to "once ever", which is never.
            #
            # This is a heuristic, and its false positives must be settled by READING the helper,
            # not by editing the code to silence it. `coaching.py`'s `_today(now)` tripped it on the
            # first run; `_today` returns `strftime("%Y-%m-%d")`, so it is a day stamp and correct —
            # the strftime lives in the helper, not at the call site the regex sees.
            frag = m.group(0)
            if any(w in frag for w in ("date()", "%Y-%m-%d", "strftime", "_today(", "_day(")):
                continue
            offenders.append(f"{py.name}: {frag[:70]}")
    check("no signal key is built from a full timestamp", not offenders, str(offenders))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
