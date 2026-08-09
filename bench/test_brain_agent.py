"""End-to-end test of AfonAgent: warmup, direct Q, time tool, session memory, fleet gate."""
import asyncio
import time
from afon.brain.agent import AfonAgent


async def main():
    a = AfonAgent()
    t0 = time.perf_counter()
    await a.warmup()
    print(f"warmup: {(time.perf_counter()-t0)*1000:.0f}ms\n")

    async def turn(text):
        t = time.perf_counter()
        r = await a.respond(text, on_progress=lambda n: print(f"   ~{n}"))
        print(f"Vazghen: {text}\nAfon : {r}  [{(time.perf_counter()-t)*1000:.0f}ms]\n")
        return r

    # 1) direct reasoning (no tool)
    await turn("In one sentence, who are you and who do you work for?")
    # 2) time tool
    await turn("What's the time right now?")
    # 3) session memory
    await turn("What did I ask you first?")
    # 4) fleet gate (not authorized -> should offer, not crash)
    r = await turn("Get me the current BTC price from the finance specialist.")
    assert "FLEET" not in r, "raw tool sentinel leaked into spoken reply!"
    print("OK: fleet sentinel not leaked; agent offered/handled gracefully.")

if __name__ == "__main__":
    asyncio.run(main())
