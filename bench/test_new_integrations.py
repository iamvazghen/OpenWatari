"""New real-world integrations (2026-07-28): Twilio phone/SMS/WhatsApp, Google Maps, Wolfram,
Google Fit, Meet-on-calendar. Hermetic — no network, no keys: verifies registration, graceful
degradation, confirm-gating of outward reach, and lazy-group activation."""

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


def main() -> None:
    from jarvis.brain.proactive import confirm_required
    from jarvis.brain.tools import groups_for_text, tool_handlers, tool_names
    from jarvis.brain.tools import fitness, maps, phone, wolfram
    from jarvis.config import settings

    # Force the unconfigured state regardless of a real .env.
    settings.twilio_account_sid = None
    settings.google_maps_api_key = None
    settings.wolfram_app_id = None
    settings.google_client_id = None

    print("[1] all new tools registered with handlers")
    names = set(tool_names())
    expected = {"place_call", "travel_time", "find_place",
                "compute", "sleep_summary", "activity_summary"}
    check("all 6 registered", expected <= names, str(expected - names))
    check("SMS/WhatsApp removed (owner's call 2026-07-29 — Telegram covers texting)",
          not ({"send_sms", "send_whatsapp"} & names))
    handlers = tool_handlers()
    check("all 6 resolvable", all(t in handlers for t in expected))

    print("\n[2] graceful degradation without keys")
    def degrades(out: str) -> bool:
        return "isn't configured yet" in out
    check("place_call degrades", degrades(asyncio.run(phone.place_call({"to": "+491", "message": "x"}))))
    # travel_time never degrades: with no Google key it must DELEGATE to the keyless OSRM engine.
    import jarvis.brain.tools.utility as util

    async def _fake_osrm(args):
        return "osrm-fallback"

    orig_osrm = util.osrm_travel_time
    util.osrm_travel_time = _fake_osrm
    try:
        check("travel_time falls back to keyless OSRM without a Google key",
              asyncio.run(maps.travel_time({"destination": "airport"})) == "osrm-fallback")
    finally:
        util.osrm_travel_time = orig_osrm
    check("find_place degrades", degrades(asyncio.run(maps.find_place({"query": "sushi"}))))
    check("compute degrades", degrades(asyncio.run(wolfram.compute({"query": "2+2"}))))
    check("sleep_summary degrades", degrades(asyncio.run(fitness.sleep_summary({}))))
    check("activity_summary degrades", degrades(asyncio.run(fitness.activity_summary({}))))

    print("\n[3] outward reach is confirm-gated; reads are not")
    check("place_call confirms", confirm_required("place_call", {}))
    check("travel_time does NOT confirm", not confirm_required("travel_time", {}))
    check("compute does NOT confirm", not confirm_required("compute", {}))

    print("\n[4] lazy groups light up on the right utterances")
    check("'call the restaurant' -> phone", "phone" in groups_for_text("call the restaurant and book a table"))
    check("'how long to the airport' -> places", "places" in groups_for_text("how long to the airport right now"))
    check("'convert 180 lbs' -> compute", "compute" in groups_for_text("convert 180 lbs to kilograms"))
    check("'how did i sleep' -> vitals", "vitals" in groups_for_text("how did I sleep last night"))
    check("plain chat stays lean", not ({"phone", "places", "compute", "vitals"}
                                        & groups_for_text("good morning, how are you")))

    print("\n[5] plan_today: owner's stated time -> main + prep reminders, day-plan recorded")
    import json
    import tempfile
    from datetime import datetime, timedelta
    from pathlib import Path as P

    import jarvis.brain.tools.reminders as rem
    import jarvis.brain.tools.routines as routines_mod
    import jarvis.brain.proactive_signals as ps

    ps._ROUTINES_PATH = P(tempfile.gettempdir()) / "test_routines_plan.json"
    ps._ROUTINES_PATH.write_text(json.dumps([
        {"key": "training", "dynamic": True, "message": "Training time, sir.",
         "prep_message": "Stretch first, sir.", "lead_minutes": 40},
    ]), encoding="utf-8")
    routines_mod._DAY_PLAN = P(tempfile.gettempdir()) / "test_day_plan_plan.json"
    routines_mod._DAY_PLAN.unlink(missing_ok=True)
    scheduled: list = []

    async def fake_set_reminder(args):
        scheduled.append(args)
        return "Done, sir."

    orig = rem.set_reminder
    rem.set_reminder = fake_set_reminder
    try:
        target = datetime.now(routines_mod.USER_TZ) + timedelta(hours=13)  # always future, ISO form
        out = asyncio.run(routines_mod.plan_today(
            {"commitment": "training", "time": target.isoformat()}))
        check("plan_today confirms", "Planned, sir" in out, out)
        check("two reminders scheduled (main + stretch prep)", len(scheduled) == 2, str(scheduled))
        mains = [datetime.fromisoformat(s["at"]) for s in scheduled]
        check("prep lands 40 min before training",
              abs((max(mains) - min(mains)).total_seconds() - 2400) < 60, str(mains))
        plan = json.loads(routines_mod._DAY_PLAN.read_text(encoding="utf-8"))
        check("day plan recorded (planning prompt goes quiet)", "training" in plan, str(plan))
        out2 = asyncio.run(routines_mod.plan_today({"commitment": "training", "time": "00:01"}))
        check("a past time is refused, not silently scheduled", "already past" in out2, out2)
    finally:
        rem.set_reminder = orig

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
