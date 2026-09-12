"""One sensing snapshot, and no fact in it can pretend to be newer than it is (26.F2, 26.F3).

The bug this file exists to prevent is silent by construction: a bare `True` from a visual check
twenty minutes ago reads exactly like one from two seconds ago, so a caller states "you're at your
desk" about a room that emptied while it was thinking. Ages are therefore not decoration here —
`describe()` is required to refuse present tense for a stale fact, and `is_stale` is required to
treat never-sensed as stale, because "I have not looked" being read as "nobody is there" is the
same bug wearing different clothes.

Also checked: that `perceive()` stays cheap and passive. It runs on every turn inside S27's 30ms
budget, so it must never open a camera — on this laptop that is also the path that has
hard-segfaulted on device enumeration. A regression there would not fail loudly; it would make
every turn slow and occasionally kill the process, so it is asserted structurally.

Hermetic: stub sensors, injected clock, module global reset between cases.

    uv run python bench/test_perception_snapshot.py
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain import perception as P  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
NOW = 1000.0

passed = failed = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}  {detail}")


class _Snap:
    def __init__(self, app="Code.exe", idle=2.0, ts=NOW):
        self.app, self.title, self.idle, self.ts = app, "a file", idle, ts


class _Presence:
    def __init__(self, snap=None, meeting=False):
        self._snap, self._meeting = snap if snap is not None else _Snap(), meeting

    def current(self):
        return self._snap

    def in_meeting(self, now=None):
        return self._meeting


def main() -> None:
    P.reset_visual()

    print("[1] one snapshot carries every sensor")
    p = P.perceive(_Presence(), now=NOW)
    for name in ("presence", "visual", "activity", "meeting"):
        check(hasattr(p, name), f"the snapshot carries `{name}`")
    check(p.presence.value == "at-screen", "a low-idle sample reads as at the screen",
          str(p.presence.value))
    check(p.activity.value == "Code.exe", "the foreground app is a fact, not a guess")
    check(p.meeting.value is False, "meeting detection is included")
    check(p.presence.source == "presence", "every fact names its source")

    idle = P.perceive(_Presence(_Snap(idle=600.0)), now=NOW)
    check(idle.presence.value == "idle", "a long idle is reported as idle, not as absence",
          str(idle.presence.value))

    print("\n[2] callers stop assembling their own — one dead sensor degrades one fact")

    class _Broken:
        def current(self):
            raise RuntimeError("store gone")

        def in_meeting(self, now=None):
            raise RuntimeError("store gone")

    b = P.perceive(_Broken(), now=NOW)
    check(b.presence.source == "presence-error", "a raising sensor is recorded as an error",
          b.presence.source)
    check(not b.presence.known, "an errored fact is not known, so nothing can read it as data")
    check(P.perceive(None, now=NOW).presence.known is False,
          "no sensor at all yields an unknown fact rather than a default")

    print("\n[3] staleness — the age is carried, and it is honoured")
    fresh = P.perceive(_Presence(), now=NOW)
    check(not fresh.presence.is_stale(NOW), "a just-sensed fact is fresh")
    check(fresh.presence.age_s(NOW) == 0.0, "age is zero at the moment of sensing")

    late = NOW + P.MAX_AGE_S["presence"] + 1
    old = P.perceive(_Presence(), now=late)
    check(old.presence.is_stale(late), "past its max age, the fact is stale")
    check(old.presence.age_s(late) > P.MAX_AGE_S["presence"], "the age is real, not a flag")

    # Never-sensed must be stale. A caller that treats unknown as fresh is the bug 26.F3 names.
    never = P.Fact()
    check(never.is_stale(NOW), "never sensed counts as stale")
    check(never.age_s(NOW) == -1.0, "never sensed has no age, and says so with -1")
    check(never.known is False, "never sensed is not known")

    # Sensed-and-false must stay distinguishable from never-sensed, or "the room is empty" and
    # "I have not looked" collapse into one answer.
    P.reset_visual()
    unlooked = P.perceive(_Presence(), now=NOW).visual
    P.record_visual("empty")
    looked = P.perceive(_Presence()).visual
    check(unlooked.known is False and looked.known is True,
          "an empty room is a fact; not having looked is not")
    check(looked.value == "empty", "the recorded verdict is what comes back")

    # A stale meeting verdict must not read as a meeting — this is how Afon would stay silent for
    # twenty minutes after a call ended.
    m_late = NOW + P.MAX_AGE_S["meeting"] + 1
    m = P.perceive(_Presence(meeting=True), now=m_late)
    check(m.meeting.value is True and m.meeting.is_stale(m_late),
          "an old meeting verdict keeps its value AND its staleness")

    print("\n[3b] describe() never presents a stale fact as current  [staleness]")
    P.reset_visual()
    d_fresh = P.perceive(_Presence(), now=NOW).describe(NOW)
    check("stale" not in d_fresh, "a fresh snapshot reads in the present tense", d_fresh)
    check("visual: not sensed" in d_fresh, "an unsensed fact says so explicitly", d_fresh)

    d_old = P.perceive(_Presence(), now=late).describe(late)
    check("stale" in d_old and "ago" in d_old, "a stale fact is stated with its age", d_old)
    check("presence: at-screen ·" not in d_old,
          "a stale fact is never stated bare, which is what would be believed", d_old)

    print("\n[4] perceive() is passive and cheap — it runs on every turn")
    src = (ROOT / "src/afon/brain/perception.py").read_text(encoding="utf-8")
    body = src[src.index("def perceive("):]
    for forbidden in ("cv2", "VideoCapture", "_capture", "await ", "requests", "httpx"):
        check(forbidden not in body, f"perceive() does not reach for `{forbidden.strip()}`")
    check(not re.search(r"^\s*(from|import)\s+.*\bcamera\b", src, re.M),
          "the sensing layer does not import the camera tool — the tool feeds IT")

    pres = _Presence()
    t0 = time.perf_counter()
    for _ in range(200):
        P.perceive(pres, now=NOW)
    per_ms = (time.perf_counter() - t0) * 1000 / 200
    check(per_ms < 30.0, f"a snapshot is {per_ms:.3f}ms, well inside the turn budget")

    print("\n[5] the camera path is what dates the visual fact")
    cam = (ROOT / "src/afon/brain/tools/camera.py").read_text(encoding="utf-8")
    check("_record_visual(" in cam, "the camera tool records its verdict for the perception layer")
    check("record_visual" in cam and "if d.get(\"available\")" in cam,
          "only an available verdict is recorded — a busy webcam is not evidence about the room")
    P.reset_visual()

    print("\n[presence fusion] 43.F3 — he is here if ANY sensor says so, away only if all agree")
    # The single-source bug: `place` was device idle and nothing else. The laptop sleeps, the edge
    # drops, he unplugs for an hour — the sample goes stale and Afon concludes the room is empty
    # while the man is sitting in it. Every sensor here is blind in a way the others are not: the
    # keyboard cannot see him on a phone call, the camera cannot see him reading on the sofa, and
    # neither of them hears him talking to Afon.
    from afon.brain.situation import _fuse_place

    now = 10_000.0

    class _FSnap:
        app, title = "Code.exe", "x"

        def __init__(self, idle, ts):
            self.idle, self.ts = idle, ts

    class _FPres:
        def __init__(self, snap):
            self._s = snap

        def current(self):
            return self._s

        def in_meeting(self, now=None):
            return False

    def snapshot(idle=None, age=0.0, visual=None, visual_age=0.0, voice=False, voice_age=0.0):
        P.reset_visual()
        if visual is not None:
            P._VISUAL = (visual, now - visual_age, "camera")
        if voice:
            P._VOICE = ("spoke", now - voice_age, "voice:laptop")
        pres = _FPres(_FSnap(idle, now - age)) if idle is not None else None
        return P.perceive(pres, now=now)

    place, why = _fuse_place(snapshot(idle=3.0), now)
    check(place == "desk" and why == "presence", f"a fresh keyboard sample alone is enough ({why})")

    # The regression. The keyboard has gone stale, and the camera saw him ten seconds ago.
    place, why = _fuse_place(snapshot(idle=3.0, age=4000.0, visual="owner", visual_age=10.0), now)
    check(place == "desk", "a stale keyboard does NOT mean away when the camera just saw him")
    check(why == "visual", f"...and the source names which sensor vouched for him ({why})")

    # The phone-call case: the laptop has been untouched for an hour, but he spoke to Afon.
    place, why = _fuse_place(snapshot(idle=3.0, age=4000.0, voice=True, voice_age=60.0), now)
    check(place == "desk", "a voice turn a minute ago outweighs an hour of not typing")
    check(why == "voice", f"...credited to the voice ({why})")

    place, why = _fuse_place(
        snapshot(idle=3.0, age=90.0, visual="owner", visual_age=5.0, voice=True, voice_age=40.0), now)
    check(place == "desk" and why.count("+") == 2, f"several agreeing sensors are all named ({why})")
    check(why == "visual+voice+presence", f"...freshest first, so the best evidence leads ({why})")

    # Away needs everyone. This is the other half: fusion must not become "never away".
    place, why = _fuse_place(snapshot(idle=3.0, age=4000.0, visual="owner", visual_age=4000.0), now)
    check(place == "away", "with every sensor stale, he really is away")
    check(why.endswith("-stale"), f"...and it says which sensor went cold first ({why})")

    place, why = _fuse_place(snapshot(idle=3.0, age=4000.0, visual="nobody", visual_age=5.0), now)
    check(place == "away", "a camera that just looked and saw nobody is not a vote for 'here'")

    place, why = _fuse_place(snapshot(), now)
    check(place == P.UNKNOWN, "with nothing sensed at all, the answer is unknown, not away")
    check(why in ("presence-empty", P.UNKNOWN), f"...and says why, rather than blaming a sensor ({why})")

    # 'I have not looked' must never be readable as 'nobody is there' — the same rule 26.F2 sets
    # for a single fact, now holding for the fusion of three.
    fused_src = (Path(__file__).resolve().parents[1]
                 / "src/afon/brain/situation.py").read_text(encoding="utf-8")
    check("_fuse_place" in fused_src and "perception.presence" not in
          fused_src.split("def assemble")[1].split("def ")[0],
          "assemble() no longer reads the presence fact directly — it goes through the fusion")

    snap = snapshot(idle=3.0, voice=True)
    check("voice" in snap.describe(now), "the voice fact is in the snapshot people read")
    check(snap.voice.max_age_s > snap.visual.max_age_s,
          "a voice turn is trusted longer than a camera frame — it is evidence of a while, not an "
          "instant")

    import afon.brain.server as srv
    check("record_voice" in (Path(__file__).resolve().parents[1]
                             / "src/afon/brain/server.py").read_text(encoding="utf-8")
          and srv is not None,
          "an inbound utterance records the voice fact")
    P.reset_visual()

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
