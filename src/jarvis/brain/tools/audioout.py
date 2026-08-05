"""Audio-output switching — "Watari, switch to my headphones".

`edge/switch_audio.py` has claimed since Phase 2 that "the brain registers it as a tool". It never
did: that module had **zero references anywhere in the repo** — no import, no tool, no script, no
test. The device machinery in `edge/audio_devices.py` was complete and simply unreachable by voice.

Two halves were missing, and the second is the one that matters:

1. the tool itself, routed to the laptop (the sound devices are there, not on the VPS brain); and
2. a path from the saved preference to the *running* stream. `resolve_output_index` is read when the
   edge worker starts, and `route_should_change` only reacts to devices appearing or vanishing — so
   saving a preference changed nothing audible until the next restart. The watchdog now watches the
   preference file too, which turns a voice command into a sub-second re-route through the rebuild
   machinery that already exists.

This is a LAZY tool group on purpose: "headphones"/"airpods"/"speakers" are distinctive enough to
trigger reliably, and keeping it out of the per-turn surface protects the streaming prefill budget.
"""

from __future__ import annotations

import json


async def _pc_audio_output_set(args: dict) -> str:
    """Save the output preference on the machine that owns the speakers. Runs ON THE LAPTOP."""
    target = str(args.get("target") or "").strip()
    if not target:
        return json.dumps({"ok": False, "out": "no device given"})
    try:
        from jarvis.edge.audio_devices import run_audio_call
        from jarvis.edge.switch_audio import set_output
    except Exception as e:  # noqa: BLE001 — audio stack absent (headless host)
        return json.dumps({"ok": False, "out": f"the audio devices aren't reachable ({type(e).__name__})"})
    try:
        # set_output enumerates PortAudio to resolve the name; that has to happen on the dedicated
        # COM-initialised thread or Bluetooth init can take the whole process down natively.
        msg = run_audio_call(set_output, target)
    except Exception as e:  # noqa: BLE001 — PortAudio/COM can throw on enumeration
        return json.dumps({"ok": False, "out": f"I couldn't switch the output ({type(e).__name__})"})
    # set_output returns a confirmation on success and an "I couldn't find…" note on failure.
    return json.dumps({"ok": not msg.lower().startswith("i couldn't find"), "out": msg})


async def _pc_audio_output_list(_args: dict) -> str:
    """List the output devices this machine can actually play through. Runs ON THE LAPTOP."""
    try:
        from jarvis.edge.audio_devices import list_devices, resolve_output_index, run_audio_call
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "out": f"the audio devices aren't reachable ({type(e).__name__})"})
    try:
        # PortAudio enumeration must run on the dedicated COM-initialised thread — off it, Bluetooth
        # device init can segfault the process natively (see audio_devices).
        devs = run_audio_call(list_devices)
        _, current = run_audio_call(resolve_output_index, None)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "out": f"I couldn't read the device list ({type(e).__name__})"})
    names = [d.name for d in devs if d.is_output]
    return json.dumps({"ok": True, "out": names, "current": current})


async def _forward(op: str, args: dict, local) -> dict:
    from jarvis.brain.tools.system import _dispatch

    try:
        raw = await _dispatch(op, args, local, timeout=60.0)
    except Exception as e:  # noqa: BLE001 — laptop dropped mid-op
        return {"ok": False, "out": f"the laptop didn't answer ({type(e).__name__})"}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {"ok": False, "out": str(raw)}


async def switch_audio_output(args: dict) -> str:
    target = str(args.get("target") or args.get("device") or "").strip()
    if not target:
        return "Which output should I use, sir — your headphones or the speakers?"
    d = await _forward("audio_output_set", {"target": target}, _pc_audio_output_set)
    out = str(d.get("out") or "")
    if not d.get("ok"):
        return out or f"I couldn't switch to '{target}', sir."
    # The watchdog notices the saved preference within one device-check tick and rebuilds the stream,
    # so this really is immediate rather than "next time I start speaking".
    return f"{out} Switching now, sir."


async def list_audio_outputs(_args: dict) -> str:
    d = await _forward("audio_output_list", {}, _pc_audio_output_list)
    if not d.get("ok"):
        return str(d.get("out") or "I couldn't read your audio devices, sir.")
    names = d.get("out") or []
    if not names:
        return "I can't see any output devices on your laptop, sir."
    current = str(d.get("current") or "").strip()
    listed = "; ".join(str(n) for n in names[:10])
    return (f"You have {len(names)} output{'s' if len(names) != 1 else ''}, sir: {listed}."
            + (f" Right now I'm using {current}." if current else ""))


SCHEMAS = [
    {"type": "function", "function": {
        "name": "switch_audio_output",
        "description": "Switch which device Watari SPEAKS through on the laptop — headphones/AirPods "
                       "or the built-in speakers. Use for 'switch to my headphones', 'play through "
                       "the speakers', 'use my AirPods', 'stop using the headphones'.",
        "parameters": {"type": "object", "properties": {
            "target": {"type": "string", "description": "'headphones', 'airpods', 'speakers', or a "
                                                        "device name fragment."}},
            "required": ["target"]}}},
    {"type": "function", "function": {
        "name": "list_audio_outputs",
        "description": "List the audio output devices on the laptop and say which one is in use. Use "
                       "for 'what can you play through?', 'which speakers are you using?'.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
]

HANDLERS = {"switch_audio_output": switch_audio_output, "list_audio_outputs": list_audio_outputs}

# The speakers are on the laptop, so device enumeration and the saved preference live there too.
LOCAL_HANDLERS = {
    "audio_output_set": _pc_audio_output_set,
    "audio_output_list": _pc_audio_output_list,
}
