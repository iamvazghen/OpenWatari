"""Screenshot transport fix — capture on the laptop, READ on the brain (works across the VPS split).

The old path captured on the laptop but OCR'd on the brain host, so on the 24/7 VPS the image never
existed where it was read. The fix: the `screenshot` PC op returns the image BYTES (base64) from the
machine with the display; the brain decodes + writes them brain-side, so read_screen_text can OCR it.

Hermetic: the capture op is monkeypatched to return a known image, so no real screen / AV / laptop.

    uv run python bench/test_screenshot_transport.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import tempfile
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
    import afon.brain.tools.system as system
    import afon.brain.tools.multimodal as mm

    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    shot = Path(tmp.name) / "sub" / "latest.jpg"   # note: parent doesn't exist yet

    # Minimal JPEG-shaped bytes (magic FF D8 … FF D9) — enough to exercise the transport + write.
    fake_jpeg = b"\xff\xd8" + b"afon-fake-screenshot-bytes" + b"\xff\xd9"

    print("[1] capture returns bytes -> brain writes them locally + confirms")
    async def fake_cap(_args):
        return json.dumps({"dims": "1920x1080", "b64": base64.b64encode(fake_jpeg).decode()})
    system.screenshot = fake_cap

    res = await mm.screenshot_screen({"path": str(shot)})
    check("screenshot_screen confirms a capture", "captured" in res.lower(), res)
    check("brain-side file was actually written", shot.exists())
    check("bytes match what the laptop sent", shot.read_bytes() == fake_jpeg)
    check("dims surfaced in the reply", "1920x1080" in res, res)

    print("\n[2] the written file is now READABLE on the brain (the whole point)")
    txt = await mm.read_screen_text({"path": str(shot)})
    check("read_screen_text no longer says 'No screenshot' (file is brain-side)",
          "no screenshot" not in txt.lower(), txt)

    print("\n[3] laptop offline / capture failed -> friendly note, no crash")
    async def empty_cap(_args):
        return "{}"
    system.screenshot = empty_cap
    off = await mm.screenshot_screen({"path": str(shot)})
    check("offline capture reported gracefully", "couldn't capture" in off.lower() or "offline" in off.lower(), off)

    print("\n[4] malformed (non-JPEG) bytes are rejected, not saved as garbage")
    async def bad_cap(_args):
        return json.dumps({"dims": "1x1", "b64": base64.b64encode(b"not-an-image").decode()})
    system.screenshot = bad_cap
    bad = await mm.screenshot_screen({"path": str(Path(tmp.name) / "bad.jpg")})
    check("malformed capture rejected", "malformed" in bad.lower(), bad)

    print("\n[5] the screenshot op is registered for the pc_agent to run laptop-side")
    check("'screenshot' in LOCAL_HANDLERS", "screenshot" in system.LOCAL_HANDLERS)

    try:
        tmp.cleanup()
    except Exception:  # noqa: BLE001
        pass

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
