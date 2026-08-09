"""Phase 0.1 — active self-repair + escalation for the reliability probe.

The old probe only LOGGED on red. This asserts the new behaviour: transient blips are repaired or
ignored, only a SUSTAINED failure pages the owner (once), and recovery is announced. Hermetic — the
push channel, the repair function, and the state file are all injected; no network, no real files
outside a temp dir.

    uv run python bench/test_self_repair.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.reliability import attempt_repair_and_escalate  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def _red(name: str, err: str = "boom") -> list[dict]:
    return [{"name": name, "ok": False, "err": err}]


def _green(name: str) -> list[dict]:
    return [{"name": name, "ok": True, "err": ""}]


async def _no_repair(_name: str) -> bool:
    return False


async def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        state = Path(td) / "esc.json"
        pushes: list[tuple[str, str]] = []

        async def push(msg: str, title: str = "Afon") -> bool:
            pushes.append((title, msg))
            return True

        print("[1] A single transient red does NOT page the owner")
        s = await attempt_repair_and_escalate(_red("llm"), push_fn=push, state_path=state, repair=_no_repair)
        check("no push on the first red", pushes == [], repr(pushes))
        check("consecutive_red counted to 1", s["llm"]["consecutive_red"] == 1, repr(s))
        check("not yet alerted", s["llm"]["alerted"] is False)

        print("\n[2] A SUSTAINED red (2nd consecutive) pages exactly once")
        s = await attempt_repair_and_escalate(_red("llm"), push_fn=push, state_path=state, repair=_no_repair)
        check("exactly one page fired", len(pushes) == 1, repr(pushes))
        check("page reads as a degradation alert", "degraded" in pushes[0][1].lower(), repr(pushes))
        check("alerted flag latched", s["llm"]["alerted"] is True)

        s = await attempt_repair_and_escalate(_red("llm"), push_fn=push, state_path=state, repair=_no_repair)
        check("a 3rd red does NOT double-page (dedup)", len(pushes) == 1, repr(pushes))

        print("\n[3] Recovery announces once, then resets")
        s = await attempt_repair_and_escalate(_green("llm"), push_fn=push, state_path=state, repair=_no_repair)
        check("recovery push fired", len(pushes) == 2 and "recover" in pushes[1][0].lower(), repr(pushes))
        check("state reset to green", s["llm"]["consecutive_red"] == 0 and s["llm"]["alerted"] is False)
        s = await attempt_repair_and_escalate(_green("llm"), push_fn=push, state_path=state, repair=_no_repair)
        check("no repeat 'recovered' when already green", len(pushes) == 2, repr(pushes))

        print("\n[4] A successful repair clears the red without ever paging")
        pushes.clear()
        state2 = Path(td) / "esc2.json"

        async def _fix(_name: str) -> bool:
            return True  # the repair took

        s = await attempt_repair_and_escalate(_red("vault"), push_fn=push, state_path=state2, repair=_fix)
        check("repaired component reports ok", s["vault"]["ok"] is True, repr(s))
        check("repaired flag set", s["vault"]["repaired"] is True)
        # Even a 2nd red repairs cleanly -> still no page.
        s = await attempt_repair_and_escalate(_red("vault"), push_fn=push, state_path=state2, repair=_fix)
        check("a self-healed component never pages the owner", pushes == [], repr(pushes))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
