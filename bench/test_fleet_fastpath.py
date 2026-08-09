"""Phase 4.4 — delegation fast-path circuit breaker, hermetic.

Some gateways refuse WS operator connects over a non-secure context
(CONTROL_UI_DEVICE_IDENTITY_REQUIRED — they want HTTPS or localhost). When that happens the handshake
fails on EVERY delegation and we pay a doomed round-trip before the CLI answers. This locks the breaker:
first failure falls back and trips it, later calls skip WS entirely, and it self-heals on expiry/success.

    uv run python bench/test_fleet_fastpath.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain import fleet  # noqa: E402
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


async def main() -> None:
    # Isolate prefs for the WHOLE test: delegate_to_fleet calls _note_success on success, which would
    # otherwise write fake routing history into the owner's real prefs (and leak into the system prompt).
    import afon.brain.prefs as prefs

    store: dict = {}
    saved_get, saved_set = prefs.get, prefs.set
    prefs.get = lambda k, d=None: store.get(k, d)
    prefs.set = lambda k, v: store.__setitem__(k, v)
    try:
        await _body(store)
    finally:
        prefs.get, prefs.set = saved_get, saved_set


async def _body(store: dict) -> None:
    print("[1] breaker basics")
    fleet._reset_ws_block()
    check("starts unblocked", fleet._ws_blocked() is False)
    fleet._block_ws("device identity required")
    check("blocks after a failure", fleet._ws_blocked() is True)
    fleet._reset_ws_block()
    check("reset clears it", fleet._ws_blocked() is False)

    print("\n[2] breaker expires (self-heals when the gateway is fixed)")
    fleet._block_ws("policy")
    fleet._WS_BLOCKED_UNTIL = 0.0  # simulate the cooldown elapsing
    check("expired breaker lets WS be tried again", fleet._ws_blocked() is False)

    print("\n[3] a doomed WS handshake trips the breaker; later calls go straight to CLI")
    saved = (settings.openclaw_delegation_enabled, settings.openclaw_token, fleet._delegate_via_cli,
             settings.openclaw_gateway_url)
    settings.openclaw_delegation_enabled = True
    settings.openclaw_token = "tok"  # force the WS branch to be considered
    # Unroutable gateway so this stays hermetic (no live call to a real gateway) and fails fast.
    settings.openclaw_gateway_url = "http://127.0.0.1:9"
    fleet._reset_ws_block()
    cli_calls = {"n": 0}

    async def fake_cli(task, timeout_s):
        cli_calls["n"] += 1
        return "answered by the CLI"

    fleet._delegate_via_cli = fake_cli
    try:
        # No real gateway here, so the WS attempt fails -> CLI fallback + breaker trips.
        r1 = await fleet.delegate_to_fleet("check the markets", timeout_s=1)
        check("first call still answers via CLI", r1 == "answered by the CLI", r1)
        check("breaker tripped after the failure", fleet._ws_blocked() is True)
        r2 = await fleet.delegate_to_fleet("check the markets again", timeout_s=1)
        check("second call also answers", r2 == "answered by the CLI", r2)
        check("both calls served by CLI", cli_calls["n"] == 2, str(cli_calls))
        # The point of the breaker: the 2nd call must not have attempted a WS connect at all.
        # We assert it indirectly — with the breaker set, the WS branch is skipped before websockets
        # is even imported, so a bogus gateway URL cannot raise.
        settings.openclaw_gateway_url = "http://256.256.256.256:1"
        r3 = await fleet.delegate_to_fleet("third", timeout_s=1)
        check("blocked path skips WS entirely (bogus URL harmless)", r3 == "answered by the CLI", r3)
    finally:
        (settings.openclaw_delegation_enabled, settings.openclaw_token,
         fleet._delegate_via_cli, settings.openclaw_gateway_url) = saved
        fleet._reset_ws_block()

    print("\n[4] routing memory still learns (Phase 4.4 'learned delegation')")
    store.clear()  # section [3]'s delegations landed here, not in the owner's real prefs
    check("no hint before anything is learned", fleet.routing_hint() == "")
    fleet._note_success("look at my stock portfolio")
    check("one success isn't a pattern yet", fleet.routing_hint() == "")
    fleet._note_success("check the etf market")
    hint = fleet.routing_hint()
    check("repeat domain becomes a routing hint", "finance/markets" in hint, hint)
    # The hint rides in EVERY system prompt against a hard 2000-tok budget, so it must stay short AND
    # bounded — an un-capped list blew the budget (2006 tok) as soon as a real user delegated twice.
    check("hint stays terse", len(hint) <= 60, f"{len(hint)} chars: {hint}")
    for extra in ("check my crypto wallet", "check my btc wallet", "refactor this code",
                  "fix the code bug", "find an apartment listing", "rent a property"):
        fleet._note_success(extra)
    many = fleet.routing_hint()
    check("hint stays capped as more domains are learned", len(many) <= 60, f"{len(many)} chars: {many}")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
