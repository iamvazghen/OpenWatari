"""Phase 3.1 — Perception: vision (see the screen), hermetic.

Live vision needs a reachable vision model (gemini via the freellmapi proxy on the VPS, or a local
proxy). This locks the LOGIC without one: `LLMClient.see()` builds a correct image_url message, returns
the first vision model's answer, and fails over; `describe_screen` captures + describes, and degrades
to OCR when no vision model answers. Fakes for the vision client + the screenshot capture; no network.

    uv run python bench/test_vision.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

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


def _msg(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


async def main() -> None:
    from afon.brain.llm import LLMClient

    print("[1] see(): builds an image_url message + returns the vision model's answer")
    llm = LLMClient()
    captured = {}

    class FakeClient:
        def __init__(self, reply, fail=False):
            self.reply, self.fail = reply, fail
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

        async def _create(self, **kw):
            captured["messages"] = kw.get("messages")
            captured["model"] = kw.get("model")
            if self.fail:
                raise RuntimeError("vision provider down")
            return _msg(self.reply)

    good = FakeClient("A code editor with a Python file open, sir.")
    llm._resolve = lambda entry: (good, entry.split(":")[-1])  # type: ignore
    out = await llm.see("QkFTRTY0", "What's on screen?", chain=["groq:vmodel"])
    check("returns the vision answer", "code editor" in out, out)
    content = captured["messages"][0]["content"]
    kinds = [c.get("type") for c in content]
    check("message carries a text part + an image_url part", kinds == ["text", "image_url"], repr(kinds))
    check("image is a base64 data URI", content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"),
          content[1]["image_url"]["url"][:30])

    print("\n[2] see(): fails over past a broken vision model to a working one")
    bad, good2 = FakeClient("x", fail=True), FakeClient("A browser on a news site, sir.")
    seq = {"n": 0}

    def _resolve2(entry):
        seq["n"] += 1
        return (bad if seq["n"] == 1 else good2), entry

    llm._resolve = _resolve2  # type: ignore
    out2 = await llm.see("Qg==", "?", chain=["broken", "working"])
    check("second model answered after the first failed", "browser" in out2, out2)

    print("\n[3] see(): raises only when EVERY vision model fails (caller can degrade)")
    llm._resolve = lambda e: (FakeClient("x", fail=True), e)  # type: ignore
    try:
        await llm.see("Qg==", "?", chain=["a", "b"])
        check("raised when all vision models fail", False)
    except RuntimeError:
        check("raised when all vision models fail", True)

    print("\n[4] describe_screen: captures + describes the screen")
    import afon.brain.tools.system as system_mod
    import afon.brain.llm as llm_mod
    from afon.brain.tools import multimodal

    async def fake_shot(_args):
        return '{"dims":"1920x1080","b64":"ZmFrZQ=="}'

    class FakeLLM:
        async def see(self, b64, prompt, **kw):
            assert b64 == "ZmFrZQ=="
            return "VS Code with test_vision.py open, sir."

    saved_shot, saved_llm = system_mod.screenshot, llm_mod.LLMClient
    system_mod.screenshot = fake_shot
    llm_mod.LLMClient = lambda: FakeLLM()
    try:
        r = await multimodal.describe_screen({"prompt": "what's open?"})
        check("returns a spoken screen description", "On your screen, sir:" in r and "VS Code" in r, r)

        print("\n[5] describe_screen: degrades to OCR when vision is unavailable")
        class BlindLLM:
            async def see(self, *a, **k):
                raise RuntimeError("no vision model reachable")

        llm_mod.LLMClient = lambda: BlindLLM()
        r2 = await multimodal.describe_screen({})
        check("falls back instead of failing blind", "read the text instead" in r2 or "couldn't interpret" in r2, r2)
    finally:
        system_mod.screenshot, llm_mod.LLMClient = saved_shot, saved_llm

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
