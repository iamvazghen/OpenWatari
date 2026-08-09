"""Phase 11 — Gmail / Calendar / Home Assistant, offline + hermetic.

These integrations need Vazghen's one-time credentials, which aren't present in a test env. The
contract we verify here is the project's golden rule: **every tool degrades gracefully** — with no
credentials each returns a short spoken "not configured" note (never a crash), the Google token
helper reports unconfigured, the new tools are all registered, and the outward-facing ones are in
the confirm tier. No network.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f" — {detail}" if detail and not ok else ""))


def degrades(out: str) -> bool:
    return isinstance(out, str) and ("isn't configured" in out or "not configured" in out)


def main() -> None:
    import afon.brain.tools.calendar as cal
    import afon.brain.tools.gmail as gmail
    import afon.brain.tools.smarthome as ha
    from afon.brain.google import access_token, configured
    from afon.brain.proactive import confirm_required
    from afon.brain.tools import tool_names
    from afon.config import settings

    # Force the UNCONFIGURED state so the degradation contract is verified regardless of whether a
    # real .env has these set (this is a hermetic test of graceful-degradation, not of live creds —
    # those are checked by bench/test_live_integrations.py).
    for _k in ("google_client_id", "google_client_secret", "google_refresh_token", "ha_url", "ha_token"):
        setattr(settings, _k, None)

    print("[1] Google OAuth helper reports unconfigured, returns no token")
    check("configured() is False with no creds", configured() is False)
    check("access_token() returns None", asyncio.run(access_token()) is None)

    print("\n[2] Gmail tools degrade gracefully (no crash, spoken note)")
    check("read_email degrades", degrades(asyncio.run(gmail.read_email({}))))
    check("draft_email degrades", degrades(asyncio.run(gmail.draft_email({"to": "a@b.c", "body": "hi"}))))
    check("send_email degrades", degrades(asyncio.run(gmail.send_email({"to": "a@b.c", "body": "hi"}))))

    print("\n[3] Calendar tools degrade gracefully")
    check("list_events degrades", degrades(asyncio.run(cal.list_events({}))))
    check("create_event degrades",
          degrades(asyncio.run(cal.create_event({"summary": "x", "start": "2026-06-12T10:00"}))))

    print("\n[4] Home Assistant tools degrade gracefully")
    check("ha_state degrades", degrades(asyncio.run(ha.ha_state({"entity": "lock.front_door"}))))
    check("ha_call degrades", degrades(asyncio.run(ha.ha_call({"domain": "light", "service": "turn_on"}))))

    print("\n[5] empty-arg tools ask for input rather than crashing")
    out = asyncio.run(ha.ha_call({}))
    check("ha_call with no domain asks for one", "domain" in out.lower() or degrades(out), out)

    print("\n[6] all six tools are registered in the brain")
    names = set(tool_names())
    expected = {"read_email", "draft_email", "send_email", "list_events", "create_event",
                "ha_state", "ha_call"}
    check("every Phase 11 tool is registered", expected <= names, str(sorted(expected - names)))

    print("\n[7] outward/sensitive tools are confirm-gated")
    check("send_email confirm-gated", confirm_required("send_email"))
    check("create_event confirm-gated", confirm_required("create_event"))
    # ha_call is domain-aware: security actuation confirms, but a light/scene flows without friction.
    check("ha_call lock confirm-gated", confirm_required("ha_call", {"domain": "lock", "service": "lock"}))
    check("ha_call alarm confirm-gated",
          confirm_required("ha_call", {"domain": "alarm_control_panel", "service": "arm_away"}))
    check("ha_call cover confirm-gated", confirm_required("ha_call", {"domain": "cover", "service": "open_cover"}))
    check("ha_call light NOT gated (smooth voice UX)",
          not confirm_required("ha_call", {"domain": "light", "service": "turn_on"}))
    check("ha_call scene NOT gated", not confirm_required("ha_call", {"domain": "scene", "service": "turn_on"}))
    check("read_email NOT confirm-gated (it's a read)", not confirm_required("read_email"))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
