"""H1.3 — "Afon, switch to my headphones" actually switches the output.

`edge/switch_audio.py` claimed in its own docstring that "the brain registers it as a tool in
Phase 2". It didn't: the module had ZERO references anywhere in the repo. The device machinery was
complete and unreachable by voice.

The subtler half is the one this file guards hardest: saving a preference used to change nothing
audible. `resolve_output_index` is read when the worker is BUILT, and `route_should_change` only
notices devices appearing/vanishing — never the preference — so the command would have "worked"
while the sound kept coming out of the old device until the next restart.

Covers: the tools exist and route to the laptop; the executor saves the preference; the watchdog
rebuilds when that file changes; and the tools stay OUT of the per-turn surface.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
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
    import afon.brain.tools.audioout as ao
    from afon.brain.pc_link import PC_LINK
    from afon.brain.tools import core_tool_schemas, groups_for_text, tool_handlers, tool_names

    print("[1] the capability exists at all — it never did before")
    names = set(tool_names())
    check("switch_audio_output registered", "switch_audio_output" in names)
    check("list_audio_outputs registered", "list_audio_outputs" in names)
    check("both have handlers", {"switch_audio_output", "list_audio_outputs"} <= set(tool_handlers()))

    print("\n[2] it routes to the laptop — the sound devices aren't on the VPS")
    seen: list[tuple[str, dict]] = []

    async def fake_forward(op, args, timeout=None):
        seen.append((op, args))
        return json.dumps({"ok": True, "out": "Output set to AirPods Pro Max."})

    real_active = type(PC_LINK).active
    type(PC_LINK).active = property(lambda self: True)
    PC_LINK.forward = fake_forward  # type: ignore[method-assign]
    try:
        msg = asyncio.run(ao.switch_audio_output({"target": "headphones"}))
        check("dispatches instead of touching this host", bool(seen), msg[:70])
        check("...as audio_output_set", seen and seen[0][0] == "audio_output_set", str(seen[:1]))
        check("...carrying the requested device", seen and seen[0][1].get("target") == "headphones")
        check("confirms the real device name", "AirPods" in msg, msg[:70])
        check("...and says it takes effect now", "now" in msg.lower(), msg[:70])

        async def list_forward(op, args, timeout=None):
            seen.append((op, args))
            return json.dumps({"ok": True, "out": ["Speakers (Realtek)", "AirPods Pro Max"],
                               "current": "AirPods Pro Max"})

        PC_LINK.forward = list_forward  # type: ignore[method-assign]
        listing = asyncio.run(ao.list_audio_outputs({}))
        check("list_audio_outputs dispatches", any(o == "audio_output_list" for o, _ in seen))
        check("...names the devices", "Realtek" in listing and "AirPods" in listing, listing[:80])
        check("...and says which is live", "Right now" in listing, listing[:80])

        async def fail_forward(op, args, timeout=None):
            return json.dumps({"ok": False, "out": "I couldn't find an output device matching 'kettle'."})

        PC_LINK.forward = fail_forward  # type: ignore[method-assign]
        bad = asyncio.run(ao.switch_audio_output({"target": "kettle"}))
        check("an unknown device is reported honestly", "couldn't find" in bad, bad[:80])

        async def boom_forward(op, args, timeout=None):
            raise ConnectionError("laptop not connected")

        PC_LINK.forward = boom_forward  # type: ignore[method-assign]
        dead = asyncio.run(ao.switch_audio_output({"target": "headphones"}))
        check("dead laptop degrades in prose, no raise", "didn't respond" in dead, dead[:80])
    finally:
        type(PC_LINK).active = real_active

    check("no target asks which one", "Which output" in asyncio.run(ao.switch_audio_output({})))

    print("\n[3] the executor saves a REAL preference on the machine with the speakers")
    from afon.edge.audio_devices import PREF_PATH, load_output_preference, save_output_preference
    prev = load_output_preference()
    try:
        got = json.loads(asyncio.run(ao._pc_audio_output_set({"target": "speakers"})))
        # Whether a matching device exists depends on the host, but either way it must answer in
        # prose and never raise — and on success the preference must actually be on disk.
        check("executor answers in prose", isinstance(got.get("out"), str) and got["out"], str(got)[:70])
        if got.get("ok"):
            check("...and the preference is persisted", load_output_preference() == "speakers",
                  str(load_output_preference()))
        else:
            check("...naming the device it couldn't find", "couldn't find" in got["out"], str(got)[:70])
        empty = json.loads(asyncio.run(ao._pc_audio_output_set({})))
        check("no device refused at the executor", empty["ok"] is False, str(empty))

        listed = json.loads(asyncio.run(ao._pc_audio_output_list({})))
        check("executor lists real outputs", listed["ok"] and isinstance(listed["out"], list),
              str(listed)[:70])

        print("\n[4] the watchdog turns a saved preference into a LIVE re-route")
        # This is the half that was missing: without it the command saves a setting and the sound
        # keeps coming out of the old device until the next restart.
        # ponytail: the watchdog watches the preference VALUE, not the file mtime. Watching mtime
        # restarted the edge every time anything rewrote the file with identical content — 103
        # restarts in the log, ~5/hour, and every one of them cut the speaker off mid-sentence.
        from afon.edge.audio_watchdog import _pref_value

        save_output_preference("speakers")
        before = _pref_value()
        check("preference value is observable", before == "speakers", repr(before))
        save_output_preference("headphones")
        check("...and moves when the preference CHANGES", _pref_value() != before,
              f"{before!r} -> {_pref_value()!r}")
        save_output_preference("headphones")
        check("...but NOT when it is rewritten unchanged (the restart storm)",
              _pref_value() == "headphones", repr(_pref_value()))

        # ...and the loop must actually ACT on it. Watching the mtime is worthless if nothing
        # rebuilds the stream, which was exactly the state this task found the system in.
        from afon.edge.audio_watchdog import AudioLivenessProbe, watch_audio_liveness

        async def drive(change: bool) -> bool:
            probe = AudioLivenessProbe()
            dead = asyncio.Event()
            task = asyncio.create_task(
                watch_audio_liveness(probe, dead, None, poll_s=0.05, grace_s=0.0,
                                     silence_limit_s=9999)
            )
            await asyncio.sleep(0.25)
            if change:
                save_output_preference("speakers" if load_output_preference() != "speakers"
                                       else "headphones")
            try:
                await asyncio.wait_for(dead.wait(), timeout=2.0)
                return True
            except asyncio.TimeoutError:
                return False
            finally:
                dead.set()
                task.cancel()

        check("the watchdog rebuilds the stream when the preference changes",
              asyncio.run(drive(True)))
        check("...and stays quiet when it doesn't (no restart loop)",
              asyncio.run(drive(False)) is False)
    finally:
        if prev is None:
            PREF_PATH.unlink(missing_ok=True)
        else:
            save_output_preference(prev)

    print("\n[5] lazy, not always-on — the per-turn prefill budget is the reason")
    for phrase in ("switch to my headphones", "use my airpods", "play through the speakers",
                   "which speakers are you using"):
        check(f"'{phrase}' activates the group", "audioout" in groups_for_text(phrase),
              str(sorted(groups_for_text(phrase))))
    core = {s["function"]["name"] for s in core_tool_schemas()}
    check("switch_audio_output is NOT in the per-turn surface", "switch_audio_output" not in core)
    check("...but stop_music still is (no trigger needed to stop sound)", "stop_music" in core)

    print("\n[6] pc_agent wires the ops up")
    import afon.edge.pc_agent as agent
    for op in ("audio_output_set", "audio_output_list"):
        check(f"pc_agent executes '{op}'", op in agent.LOCAL_HANDLERS)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
