"""T6 multi-modal tools — laptop-side screenshot + OCR (precursor to glasses camera + display).

For "what's on my screen" / "read this menu" / "what does this say" queries. Takes a fresh
screenshot of the laptop display via the pc_agent executor and (if pytesseract is installed)
runs OCR on it. Falls back to returning the screenshot path so a vision-capable LLM can read
it on the next turn.

The realised "eyes" (Phase 3): this module SEES the SCREEN (screenshot -> ``describe_screen`` VLM /
``read_screen_text`` OCR); ``tools/camera.py`` sees the ROOM (laptop/phone webcam -> ``look_around``
VLM + ``visual_presence`` local face detection). MentraOS glasses were NOT pursued (no SDK/account —
the half-finished client was deleted); the phone camera is the mobile eye and reuses the same
``LLMClient.see`` surface, so nothing here has to change if a wearable ever lands.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from jarvis.brain.tools.base import tool_error


_DEFAULT_SHOT = Path.home() / ".jarvis" / "screenshots" / "latest.jpg"


async def screenshot_screen(args: dict) -> str:
    """Capture the screen and store it on the BRAIN host so it can actually be read/OCR'd.

    Captures via the ``screenshot`` PC op, which runs on the machine with the display (the laptop)
    and returns the image BYTES — so this works even when the brain is the 24/7 VPS and the screen
    is on the laptop (the old code saved on the laptop but read on the brain, which never worked
    across that split). ``read_screen_text`` then OCRs the brain-side file."""
    import base64
    import json

    path = Path((args.get("path") or str(_DEFAULT_SHOT)).strip() or str(_DEFAULT_SHOT))
    try:
        from jarvis.brain.tools.system import screenshot as _cap

        raw = await _cap({})
        try:
            data = json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            data = {}
        b64 = data.get("b64")
        if not b64:
            return "I couldn't capture the screen, sir — the laptop may be offline or its executor isn't running."
        img = base64.b64decode(b64)
        if img[:2] != b"\xff\xd8":  # not a JPEG — capture failed
            return "The screen capture came back malformed, sir; I couldn't save it."
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(img)
        dims = data.get("dims") or ""
        return f"Screenshot captured ({dims}) and saved, sir — I can read it now."
    except Exception as e:  # noqa: BLE001
        return tool_error("screenshot", e)


async def read_screen_text(args: dict) -> str:
    """OCR the most recent screenshot. Returns up to ~2000 chars of extracted text.

    If pytesseract isn't installed, falls back to the screenshot path so a vision LLM can read it.
    """
    path = (args.get("path") or str(_DEFAULT_SHOT)).strip() or str(_DEFAULT_SHOT)
    p = Path(path)
    if not p.exists():
        return f"No screenshot at {path}, sir — run screenshot_screen first."
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore
    except ImportError:
        return (f"Screenshot at {path}, sir — pytesseract / Pillow aren't installed, so I can't "
                "OCR it here. The image is on disk for me to read on the next turn.")
    try:
        img = Image.open(p)
        text = pytesseract.image_to_string(img)
        text = (text or "").strip()
        if not text:
            return f"No readable text found in {path}, sir."
        if len(text) > 2000:
            text = text[:1997].rstrip() + "…"
        return f"On screen:\n{text}"
    except Exception as e:  # noqa: BLE001
        return tool_error("OCR", e)


async def describe_screen(args: dict) -> str:
    """Phase 3.1 — SEE the screen, not just OCR it. Capture the laptop screen and have a vision model
    describe/answer about it: "what's on my screen", "what's this error", "read me that dialog". Falls
    back to OCR text if no vision model is reachable, so it degrades instead of going blind.
    """
    import base64
    import json

    prompt = (args.get("prompt") or "").strip() or (
        "Describe what's on this screen concisely for the owner. If there's an error, a dialog box, or "
        "important text, read it out. Keep it to one or two sentences unless there's a lot to report."
    )
    try:
        from jarvis.brain.tools.system import screenshot as _cap

        raw = await _cap({})
        try:
            data = json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            data = {}
        b64 = data.get("b64")
        if not b64:
            return "I couldn't capture the screen, sir — the laptop may be offline or its executor isn't running."
        from jarvis.brain.llm import LLMClient

        try:
            desc = await LLMClient().see(b64, prompt)
            return f"On your screen, sir: {desc}"
        except Exception as e:  # noqa: BLE001 — vision down -> degrade to OCR rather than fail blind
            logger.warning(f"describe_screen: vision unavailable ({type(e).__name__}); falling back to OCR")
            try:
                img = base64.b64decode(b64)
                _DEFAULT_SHOT.parent.mkdir(parents=True, exist_ok=True)
                _DEFAULT_SHOT.write_bytes(img)
                ocr = await read_screen_text({"path": str(_DEFAULT_SHOT)})
                return f"(I can't see it properly right now, sir, so I read the text instead.) {ocr}"
            except Exception:  # noqa: BLE001
                return "I captured the screen but couldn't interpret it, sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("describe_screen", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "describe_screen",
            "description": (
                "SEE and describe the laptop screen using vision (not just OCR). Use for 'what's on "
                "my screen', 'what am I looking at', 'what's this error', 'read me that dialog', "
                "'describe what you see'. Understands images/layout, not only text. Optional 'prompt' "
                "to ask something specific about the screen."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description":
                               "Optional specific question about the screen (default: describe it)."},
                },
                "required": [],   # every argument genuinely optional — stated, not left implied
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot_screen",
            "description": (
                "Take a screenshot of the laptop screen and save it to a PNG path. Use for "
                "'what's on my screen', 'show me what you see', 'capture this', or as a precursor "
                "to reading screen text via OCR. The image is on disk for the next turn."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description":
                             "Where to save the PNG (default ~/.jarvis/screenshots/latest.png)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_screen_text",
            "description": (
                "OCR the most recent laptop screenshot and return extracted text. Use for "
                "'read the screen', 'what does this say', 'read this menu', 'transcribe what's "
                "on my monitor'. Requires pytesseract + Pillow on the laptop; falls back to the "
                "screenshot path when missing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description":
                             "Screenshot path (default ~/.jarvis/screenshots/latest.png)."},
                },
                "required": [],
            },
        },
    },
]

HANDLERS = {"describe_screen": describe_screen, "screenshot_screen": screenshot_screen,
            "read_screen_text": read_screen_text}