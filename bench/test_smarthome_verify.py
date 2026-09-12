"""28.F2/28.F3 — Afon reports the state he observed, and confirms what isn't only his.

Home Assistant answers 200 to a service call for a device that is unplugged, out of battery, or
simply not listening, and hands back an empty change list. The old reply was "Done, sir —
light.turn_on on kitchen. 0 entity change(s)." — a claim about the world assembled from the absence
of an error, which is the same shape as a deploy script reporting success because the upload
finished. The owner walks into a dark kitchen having been told the light is on.

What this asserts:

  * the reply names the state that came back, not the command that went out;
  * when nothing changed, Afon goes and READS the device rather than assuming;
  * when the device did not do it, he says so instead of rounding up to done;
  * when he cannot read it back at all, he says he cannot confirm — the one answer that is never
    available is a confident one;
  * heating and locks confirm first, a lamp of his own does not, and a lamp in a room other people
    live in does once he has said which rooms those are.

Hermetic: the HTTP layer is replaced. No Home Assistant, no network, no token needed.

    uv run python bench/test_smarthome_verify.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


async def main() -> None:
    from afon.brain.tools import smarthome as sh
    from afon.brain.proactive import confirm_required
    from afon.config import settings

    settings.ha_url = "http://ha.local:8123"
    settings.ha_token = "test-token"

    posts: list[str] = []
    gets: list[str] = []
    state_reply = {"entity_id": "light.kitchen", "state": "on",
                   "attributes": {"friendly_name": "Kitchen light"}}
    post_reply: list = []
    get_raises = False

    async def fake_post(url, **kw):
        posts.append(url)
        return _Resp(post_reply)

    async def fake_get(url, **kw):
        gets.append(url)
        if get_raises:
            raise ConnectionError("ha unreachable")
        return _Resp(state_reply)

    sh.http_post, sh.http_get = fake_post, fake_get

    print("[1] the reply is the state that came back, not the command that went out")
    post_reply = [state_reply]
    out = await sh.ha_call({"domain": "light", "service": "turn_on", "entity": "light.kitchen"})
    check("it names the device by its friendly name", "Kitchen light" in out, out)
    check("...and the state it is actually in", "is on" in out, out)
    check("it does not simply say the command succeeded", "Done, sir" not in out, out)
    check("no extra read was needed — the response already said", gets == [], str(gets))

    print("\n[2] when nothing changed, he goes and looks")
    gets.clear()
    post_reply = []
    out = await sh.ha_call({"domain": "light", "service": "turn_on", "entity": "light.kitchen"})
    check("an empty change list triggers a read-back",
          any("states/light.kitchen" in g for g in gets), str(gets))
    check("...and the answer is what the read found", "is on" in out, out)

    print("\n[3] when the device did NOT do it, he says so")
    gets.clear()
    post_reply = []
    state_reply = {"entity_id": "light.kitchen", "state": "off",
                   "attributes": {"friendly_name": "Kitchen light"}}
    out = await sh.ha_call({"domain": "light", "service": "turn_on", "entity": "light.kitchen"})
    check("the mismatch is reported, not rounded up to done",
          "still reads off" in out, out)
    check("...and he names it as a fault worth acting on", "wrong with it" in out, out)

    print("\n[4] when he cannot read it back, he refuses to claim it worked")
    get_raises = True
    post_reply = []
    out = await sh.ha_call({"domain": "lock", "service": "lock", "entity": "lock.front_door"})
    check("the failure to verify is stated plainly", "couldn't read it back" in out, out)
    check("...and he does not say it worked", "can't tell you it worked" in out, out)
    get_raises = False

    print("\n[5] a service with no single target claims nothing about one")
    post_reply = [state_reply, state_reply]
    out = await sh.ha_call({"domain": "scene", "service": "turn_on", "entity": ""})
    check("a whole-domain call reports what it touched", "2 entity change(s)" in out, out)
    check("...and names no state it did not check", "is on" not in out, out)

    print("\n[6] a service with no unambiguous target state is never graded")
    gets.clear()
    post_reply = []
    state_reply = {"entity_id": "light.kitchen", "state": "off",
                   "attributes": {"friendly_name": "Kitchen light"}}
    out = await sh.ha_call({"domain": "light", "service": "toggle", "entity": "light.kitchen"})
    check("a toggle reports the state it found, with no verdict",
          "is off" in out and "wrong with it" not in out, out)

    print("\n[7] what isn't only his, he confirms first  [28.F3]")
    check("a lock confirms", confirm_required("ha_call", {"domain": "lock", "service": "lock"}))
    check("an alarm confirms",
          confirm_required("ha_call", {"domain": "alarm_control_panel", "service": "arm_away"}))
    check("a cover confirms",
          confirm_required("ha_call", {"domain": "cover", "service": "close_cover"}))
    # Heating is not his alone either: everyone in the building lives in the temperature he sets,
    # and unlike a lamp nobody else can undo it from the wall.
    check("heating confirms", confirm_required("ha_call", {"domain": "climate",
                                                           "service": "set_temperature"}))
    check("the hot water confirms", confirm_required("ha_call", {"domain": "water_heater",
                                                                 "service": "set_temperature"}))
    check("his own lamp does not — gating it teaches him to say yes without reading",
          not confirm_required("ha_call", {"domain": "light", "service": "turn_on",
                                           "entity": "light.desk"}))

    print("\n[8] shared rooms are configured, never guessed")
    settings.ha_shared_areas = ""
    check("with nothing configured, no light is treated as shared",
          not confirm_required("ha_call", {"domain": "light", "service": "turn_on",
                                           "entity": "light.living_room"}))
    settings.ha_shared_areas = "living_room, hallway"
    check("a light in a room he named as shared confirms",
          confirm_required("ha_call", {"domain": "light", "service": "turn_on",
                                       "entity": "light.living_room"}))
    check("...and so does another room on the list",
          confirm_required("ha_call", {"domain": "switch", "service": "turn_on",
                                       "entity": "switch.hallway_fan"}))
    check("a room he did not name still flows without friction",
          not confirm_required("ha_call", {"domain": "light", "service": "turn_on",
                                           "entity": "light.desk"}))
    settings.ha_shared_areas = ""

    print("\n[9] the two sensitive lists cannot drift apart")
    from afon.brain.proactive import _HA_SENSITIVE_DOMAINS

    check("the policy layer and the tool agree on what is sensitive",
          _HA_SENSITIVE_DOMAINS == sh.SENSITIVE_DOMAINS,
          f"{sorted(_HA_SENSITIVE_DOMAINS)} vs {sorted(sh.SENSITIVE_DOMAINS)}")

    print("\n[10] with no token it degrades, it does not guess")
    settings.ha_url = settings.ha_token = None
    from afon.brain.tools.base import is_not_configured

    out = await sh.ha_call({"domain": "light", "service": "turn_on", "entity": "light.kitchen"})
    check("an unconfigured Home Assistant says so", is_not_configured(out), out)
    out = await sh.ha_state({"entity": "light.kitchen"})
    check("...and so does a read", is_not_configured(out), out)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
