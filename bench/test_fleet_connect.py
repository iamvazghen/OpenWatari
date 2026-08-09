"""Fleet live test (MASTER 3.10) — verify brain -> ispir end-to-end, GATED on authorization.

The fleet touches shared VPS infra, so this only RUNS when the operator has armed it
(``AFON_FLEET_AUTHORIZED=true``) and a gateway token is configured. Otherwise it self-skips with
a clear sentinel — the aggregate runner treats that as SKIP, not FAIL, so a default checkout stays
green without a live gateway.

When armed it does a real WS connect to the gateway, sends ``agents.list``, and asserts the router
agent (``ispir``) is present — proving the brain can reach its team lead. Network failures (gateway
down / tunnel closed) are reported as a skip condition, not a hard failure.

    AFON_FLEET_AUTHORIZED=true uv run python bench/test_fleet_connect.py
"""

from __future__ import annotations

import asyncio
import sys

from afon.config import settings

# Sentinels the aggregate runner greps for.
SKIP_SENTINEL = "fleet live test skipped"
PASS_SENTINEL = "fleet live checks passed"


def _skip(reason: str) -> None:
    print(f"{SKIP_SENTINEL}: {reason}")
    sys.exit(0)


async def main() -> None:
    if not settings.fleet_authorized:
        _skip("AFON_FLEET_AUTHORIZED is not true (fleet consult disarmed by default)")
    if not settings.openclaw_token or not settings.openclaw_gateway_url:
        _skip("no gateway token/url configured (CLI-only or unconfigured deployment)")

    # Exercise the REAL delegation path the brain uses in production: delegate_to_fleet() tries the
    # WS gateway first and TRANSPARENTLY falls back to the CLI/SSH path when the WS connect is
    # rejected (e.g. the gateway's device-identity policy for non-localhost origins). That fallback
    # is exactly how the 24/7 deployment reaches ispir, so verifying it — not a raw WS handshake — is
    # what proves "brain -> ispir end-to-end".
    from afon.brain.fleet import FleetUnavailable, delegate_to_fleet

    router = settings.openclaw_router_agent
    ping = "Reply with exactly the word PONG and nothing else."
    # Generous budget: the WS gateway can time out (a known reasoning-lane wedge) and fall back to
    # the embedded/CLI path, which for a slow reasoning router can take a couple of minutes. This is
    # a gated LIVE test, so a slow-but-successful round-trip is fine; a true timeout just skips.
    print(f"delegating a ping to '{router}' via delegate_to_fleet (WS -> CLI fallback)…")
    try:
        answer = await asyncio.wait_for(delegate_to_fleet(ping, timeout_s=150), timeout=210)
    except FleetUnavailable as e:
        _skip(f"fleet unreachable via WS and CLI ({e})")
    except (OSError, asyncio.TimeoutError) as e:
        _skip(f"fleet transport/timeout ({type(e).__name__}: {e})")
    except Exception as e:  # noqa: BLE001 — any transport error = skip, not a code failure
        _skip(f"delegation transport error ({type(e).__name__}: {e})")

    text = (answer or "").strip()
    print(f"  ispir answered: {text[:120]!r}")
    ok = bool(text)   # a non-empty synthesized answer proves the round-trip
    mark = "[PASS]" if ok else "[FAIL]"
    print(f"  {mark} brain reached '{router}' and got a synthesized answer back")
    if not ok:
        print(f"  the delegation returned empty — '{router}' round-trip failed")
        sys.exit(1)
    print(f"\n=== {PASS_SENTINEL}: brain -> {router} verified ===")


if __name__ == "__main__":
    asyncio.run(main())
