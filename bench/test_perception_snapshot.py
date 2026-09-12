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

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
