"""I1 — face as a SECOND factor on privileged actions (fail-open by design).

Offline & hermetic: the camera is stubbed, no hardware touched.

Why this exists: the voice gate cannot carry authorisation alone. On the live mic the owner's accept
median is 0.33 against an impostor ceiling of 0.30, and `logs/edge.log` shows 61 speaker-gate accepts
with 0 rejections — ambient television reached the brain as though the owner had spoken it. So for
actions that are confirm-gated anyway, "he said yes" must also mean he was THERE.

The invariant that matters more than the feature: this may only ever ADD a refusal. A camera that is
missing, busy, unenrolled, offline or throwing must leave behaviour exactly as it was — an assistant
that stops obeying its owner because a webcam is in use is worse than no second factor.

    uv run python bench/test_face_second_factor.py
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


async def main() -> None:
    import jarvis.brain.tools.camera as CAM
    from jarvis.brain.agent import JarvisAgent
    from jarvis.brain.proactive import confirm_required
    from jarvis.config import settings

    agent = JarvisAgent()
    calls: list[int] = []

    def stub(verdict: dict | None, boom: bool = False):
        async def _v() -> dict:
            calls.append(1)
            if boom:
                raise RuntimeError("camera exploded")
            return verdict
        CAM.verify_owner_present = _v

    print("[1] the owner is verified -> the action proceeds")
    stub({"available": True, "matched": True, "faces": 1})
    check("no refusal when the camera recognises him",
          await agent._face_second_factor("send_email", {}) is None)

    print("\n[2] FAIL-OPEN: can't tell must behave exactly as before")
    stub({"available": False, "matched": False, "faces": 0})
    check("no camera / not enrolled / laptop offline -> proceeds",
          await agent._face_second_factor("send_email", {}) is None)
    stub(None, boom=True)
    check("an exception inside the check -> proceeds (never blocks the owner)",
          await agent._face_second_factor("send_email", {}) is None)

    print("\n[3] a POSITIVE negative refuses — and says which negative it is")
    stub({"available": True, "matched": False, "faces": 0})
    empty = await agent._face_second_factor("send_email", {})
    check("empty room -> refused", bool(empty), str(empty))
    check("names the real reason (nobody there)", empty and "nobody at the desk" in empty, str(empty))
    check("forbids claiming success", empty and "do NOT" in empty and "BLOCKED" in empty)

    stub({"available": True, "matched": False, "faces": 2})
    other = await agent._face_second_factor("send_email", {})
    check("someone else present -> refused", bool(other), str(other))
    check("distinguishes 'not him' from 'nobody'", other and "isn't the owner" in other, str(other))

    print("\n[4] it is scoped to PRIVILEGED actions only")
    # A camera burst on every turn would be a privacy and latency cost with no safety return, so the
    # gate must only ever consult it for calls that are confirm-gated anyway.
    check("send_email is confirm-gated (so it IS second-factored)", confirm_required("send_email", {}))
    check("get_time is not gated (so it is NOT)", not confirm_required("get_time", {}))
    check("web_search is not gated", not confirm_required("web_search", {"query": "x"}))

    print("\n[5] the camera is not consulted for an ordinary tool")
    calls.clear()
    stub({"available": True, "matched": False, "faces": 0})    # would refuse IF consulted
    agent._registry["harmless"] = lambda _a: "ok"
    msgs: list = []
    outs = await agent._execute_calls(msgs, [{"id": "c1", "name": "get_time", "arguments": "{}"}])
    check("no camera check for a non-gated call", not calls, f"{len(calls)} camera checks")
    check("and the call still ran", outs and outs[0].get("ok") is not False, str(outs)[:90])

    print("\n[6] the whole thing is switchable off in one flag")
    check("face_second_factor exists and defaults ON", settings.face_second_factor is True)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
