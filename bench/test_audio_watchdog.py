"""Audio watchdog: trips on a dead mic / vanished output device, stays quiet on a live stream.

Guards the recurring "I say hey watari and nothing comes" bug: a device change (AirPods connect/
disconnect) stales the mic/speaker streams; the watchdog must detect that and trigger a fresh restart.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis.edge.audio_watchdog import (  # noqa: E402
    AudioLivenessProbe, output_device_present, watch_audio_liveness,
)

_ok = _fail = 0


def check(cond, label):
    global _ok, _fail
    if cond:
        _ok += 1
    else:
        _fail += 1
        print(f"  FAIL: {label}")


async def _run():
    # A) a DEAD mic (gap past silence_limit) MUST trip a restart so Watari recovers from deafness.
    # auto_route=False: this tests the MIC path only — otherwise real headphones on the dev machine
    # (AirPods connected) make route_should_change want to re-route, which is a different trip reason.
    dead = asyncio.Event()
    stalled = AudioLivenessProbe()
    stalled.last_input = time.monotonic() - 100
    await watch_audio_liveness(stalled, dead, None, silence_limit_s=20, grace_s=0, poll_s=0.05,
                               auto_route=False)
    check(dead.is_set(), "a dead mic (long gap) trips a restart")

    # B) does NOT trip on a live mic (auto_route=False so the dev machine's real headphone state can't
    # false-trip this: with auto-route ON and AirPods connected the watchdog legitimately re-routes).
    dead2 = asyncio.Event()
    live = AudioLivenessProbe()  # last_input = now
    try:
        await asyncio.wait_for(
            watch_audio_liveness(live, dead2, None, silence_limit_s=20, grace_s=0, poll_s=0.05,
                                 auto_route=False),
            timeout=0.4,
        )
    except asyncio.TimeoutError:
        pass
    check(not dead2.is_set(), "stays quiet while the mic is live")

    # C) DOES trip when the bound output device vanishes (needs 2 consecutive misses = hysteresis)
    dead3 = asyncio.Event()
    live2 = AudioLivenessProbe()
    await watch_audio_liveness(live2, dead3, "NoSuchDevice ZZZ 9999",
                               silence_limit_s=999, grace_s=0, poll_s=0.05, device_check_every=1)
    check(dead3.is_set(), "trips when the bound output device vanished (headphones unplugged)")

    # D) output_device_present fail-open: unknown name → True (never false-trip on a missing name)
    check(output_device_present(None) is True, "output_device_present(None) fails open")

    # E) resume from sleep: a wall-clock jump the poll interval can't explain means the machine was
    # suspended, so every audio stream and cloud socket we hold is stale. Windows gives no usable
    # resume event here (S0 standby emits none; Kernel-Power 507 fires ~8x/day for maintenance wakes),
    # so this in-process check is the detector. Regression guard for a full day spent silently deaf.
    import jarvis.edge.audio_watchdog as _m

    dead4 = asyncio.Event()
    live3 = AudioLivenessProbe()
    real_time, n = _m.time.time, {"c": 0}

    def _slept():
        n["c"] += 1
        return real_time() + (1200 if n["c"] > 1 else 0)   # 2nd reading is 20 min later

    _m.time.time = _slept
    try:
        await watch_audio_liveness(live3, dead4, None, silence_limit_s=999, grace_s=0, poll_s=0.05,
                                   auto_route=False)
    finally:
        _m.time.time = real_time
    check(dead4.is_set(), "trips after a sleep/resume wall-clock jump")

    # ...and a jump SHORTER than the limit (a brief maintenance wake) must not restart him.
    dead5 = asyncio.Event()
    live4 = AudioLivenessProbe()
    n2 = {"c": 0}

    def _blinked():
        n2["c"] += 1
        return real_time() + (30 if n2["c"] > 1 else 0)

    _m.time.time = _blinked
    try:
        await asyncio.wait_for(
            watch_audio_liveness(live4, dead5, None, silence_limit_s=999, grace_s=0, poll_s=0.05,
                                 auto_route=False),
            timeout=0.4,
        )
    except asyncio.TimeoutError:
        pass
    finally:
        _m.time.time = real_time
    check(not dead5.is_set(), "a brief maintenance wake does NOT restart the edge")


asyncio.run(_run())
print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
