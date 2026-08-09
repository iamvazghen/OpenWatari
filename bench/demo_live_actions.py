"""Live action demo — AFON performs every integration himself (not the script).

This drives the real `AfonAgent`: each step is a natural-language command, and Afon's own
brain decides which tool to call and runs it. Watch his spoken replies + the tool log.

    uv run python bench/demo_live_actions.py

It will: play Michael Jackson on YouTube Music (free), message your Telegram Saved Messages, send a
panda GIF there, push a phone notification, and web-search + open your browser for the cheapest
Air Jordan 1 OG Low US-12.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.agent import AfonAgent  # noqa: E402


async def main() -> None:
    # A few extra tool iterations so multi-step turns (search -> open browser -> read) fit.
    afon = AfonAgent(max_tool_iters=6)
    await afon.warmup()

    async def say(label: str, command: str, settle: float = 0.0) -> None:
        print("\n" + "=" * 78)
        print(f"  Vazghen: {command}")
        print("-" * 78)
        reply = await afon.respond(command, on_progress=lambda n: print(f"   …{n}"))
        print(f"  Afon : {reply}")
        if settle:
            print(f"   (waiting {settle:.0f}s …)")
            await asyncio.sleep(settle)

    t0 = time.perf_counter()

    # 1) Play Michael Jackson on YouTube Music (free, music-tuned — no account needed).
    await say("music-play",
              "Play a Michael Jackson song — pick one. Just do it.", settle=4)

    # 2) Telegram Saved Messages text.
    await say("tg-text",
              "Send a message to my Telegram Saved Messages saying: "
              "'Afon integration test — all systems go.' Send it now, no confirmation needed.")

    # 3) Telegram panda GIF to Saved Messages.
    await say("tg-gif",
              "Now send a panda GIF to my Telegram Saved Messages, with the caption "
              "'a panda for you'. Go ahead.")

    # 4) ntfy phone push.
    await say("push",
              "Push a notification to my phone that says the integration test is running. Do it now.")

    # 5) Web search + open the visible browser to showcase the deal.
    await say("shopping",
              "Find the cheapest men's Nike Air Jordan 1 OG Low in US size 12. First search the "
              "web for the best current price, then OPEN MY BROWSER to a listing page so I can see "
              "it on screen. Tell me the price you found. Go ahead and do all of it.")

    print("\n" + "=" * 78)
    print(f"  demo complete in {time.perf_counter() - t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
