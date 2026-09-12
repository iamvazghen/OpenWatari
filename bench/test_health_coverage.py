"""J3.7 — "nine health communities, no facade". Eight of the nine are not the same question.

The graph groups by name and found: reliability · voice_health · uptime_watch · diagnose · errors ·
Metrics · watch_audio_liveness · singleton · _supervisor. Read across them and they split three
ways, and only one of those splits is a defect:

  * **Different hosts.** `voice_health`, `watch_audio_liveness` and `_supervisor` are EDGE organs —
    another process on another machine. A facade over them and the brain's checks would mean the
    brain importing edge internals, which `test_layering.py` forbids, correctly. This is not a
    missing abstraction; it is a process boundary.
  * **Different questions.** `Metrics` counts, `diagnose` reports what recently ERRORED, `errors`
    types failures, `singleton` prevents two of something. Merging any of these loses information.
  * **The same question, twice** — brain component health, asked structurally (`health.check()`) and
    functionally (`reliability.health_probe()`). J3.6 already resolved that one, and the guarantee
    it settled on was agreement, not sameness.

So what was left is the thing a facade would have accidentally fixed: **coverage honesty.**
`summarize()` opened with "All systems nominal, sir" over a three-component check that never looked
at the laptop, the LLM chain, or the edge at all. The owner can act on that sentence. It is the same
defect as a status page showing green because it never asked the question — and it is worse spoken,
because there is no component list next to it to contradict it.

What this asserts:

  * the healthy sentence NAMES its coverage instead of claiming totality;
  * every component in the snapshot is named in that sentence — so adding a check cannot silently
    fall out of what he says, and the sentence cannot imply a check that does not exist;
  * `pc_link` is in the snapshot (a dark laptop means device control, camera and screen are all
    unreachable — that is not "nominal") but raises NO proactive signal, because a closed laptop is
    a normal state of the world and paging about it would be nagging.

Hermetic: every probe is stubbed; no vault, no network, no laptop.

    uv run python bench/test_health_coverage.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain import health  # noqa: E402

passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class _Link:
    """Stands in for PcLink.

    It used to expose `active` and `host` as METHODS, which is how the real defect survived for
    months: health.py called `PC_LINK.active()`, the stub answered, and against the real class —
    where both are properties — that call raised TypeError, was swallowed by the surrounding
    except, and reported the laptop as down with "pc-link check error" whenever it was up. A stub
    that disagrees with the class it stands for turns a green test into evidence of nothing, so
    the shape is asserted below rather than assumed.
    """

    def __init__(self, up: bool):
        self.up = up

    @property
    def active(self) -> bool:
        return self.up

    @property
    def host(self) -> str | None:
        return "laptop-01" if self.up else None

    @property
    def silent_for(self) -> float:
        return 0.0

    async def reachable(self, timeout: float = 3.0) -> bool:
        return self.up


async def main() -> None:
    from afon.brain import pc_link as pl

    print("[0] the stub agrees with the class it stands for")
    real = pl.PcLink
    for name in ("active", "host", "silent_for"):
        check(f"{name} is a property on both",
              isinstance(getattr(real, name), property)
              and isinstance(getattr(_Link, name), property),
              f"{name}: real={type(getattr(real, name)).__name__}, "
              f"stub={type(getattr(_Link, name)).__name__}")
    check("reachable is awaitable on both",
          callable(real.reachable) and callable(_Link.reachable))


    saved = (health._check_vault, health._check_ticker, health._check_cache, pl.PC_LINK)

    async def _vault_ok():
        return True, "vault ok"

    async def _ticker_ok():
        return True, "ticker reachable"

    try:
        health._check_vault = _vault_ok
        health._check_ticker = _ticker_ok
        health._check_cache = lambda: (True, "cache backend: memory")

        print("[1] the healthy sentence names its coverage instead of claiming everything")
        pl.PC_LINK = _Link(True)
        snap = await health.check()
        line = health.summarize(snap)
        check("all systems" not in line.lower(),
              "no 'all systems nominal' over a partial check", line)
        check("everything i can see" in line.lower() or "i can see" in line.lower(),
              "…it is scoped to what was actually looked at", line)

        print("\n[2] every component checked is named in what he says")
        missing = [n for n in snap if health._SPOKEN.get(n, n).split()[-1].lower() not in line.lower()]
        check(not missing, "each snapshot component appears in the spoken summary", str(missing))
        check(set(health._SPOKEN) >= set(snap),
              "…and no component can be added without a phrase for it",
              str(set(snap) - set(health._SPOKEN)))

        print("\n[3] the laptop is part of the picture — a dark laptop is not 'nominal'")
        check("pc_link" in snap, "pc_link is in the snapshot", str(list(snap)))
        check(snap["pc_link"]["ok"] is True, "connected laptop reads healthy", str(snap["pc_link"]))
        pl.PC_LINK = _Link(False)
        snap_down = await health.check()
        check(snap_down["pc_link"]["ok"] is False, "disconnected laptop reads unhealthy",
              str(snap_down["pc_link"]))
        line_down = health.summarize(snap_down)
        check("partly degraded" in line_down and "laptop" in line_down.lower(),
              "…and he says so when asked", line_down)

        print("\n[4] but it never pages him about it")
        sigs = await health.health_signals()
        check(not [s for s in sigs if "pc" in s.key or "laptop" in s.message.lower()],
              "a closed laptop raises no proactive signal", str([s.key for s in sigs]))

        print("\n[5] a real fault still raises, and still says which")
        async def _vault_bad():
            return False, "vault path unreadable"

        health._check_vault = _vault_bad
        snap_bad = await health.check()
        sigs = await health.health_signals()
        check(any(s.key == "health-vault" for s in sigs), "a broken vault raises its signal",
              str([s.key for s in sigs]))
        check("vault" in health.summarize(snap_bad), "…and is named in the summary",
              health.summarize(snap_bad))
    finally:
        (health._check_vault, health._check_ticker, health._check_cache, pl.PC_LINK) = saved

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
