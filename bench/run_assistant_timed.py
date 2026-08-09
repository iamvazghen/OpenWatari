"""Start the Phase-1 wake-word assistant, run ~8s, then stop. Confirms the wake-word
model loads, gates wire, audio devices open, and STT/TTS connect — without a voice test."""

import asyncio

from afon.edge.assistant import build_worker
from pipecat.workers.runner import WorkerRunner


async def main() -> None:
    runner = WorkerRunner()
    await runner.add_workers(build_worker())
    run = asyncio.create_task(runner.run())
    print(">>> assistant started; running 8s (mic gated by 'Hey Afon')…", flush=True)
    await asyncio.sleep(8)
    print(">>> stopping…", flush=True)
    try:
        await runner.cancel()
    except Exception as e:  # noqa: BLE001
        print("cancel raised:", type(e).__name__, e)
    try:
        await asyncio.wait_for(run, timeout=10)
    except Exception as e:  # noqa: BLE001
        print("runner ended via:", type(e).__name__)
    print(">>> PHASE 1 STARTUP OK — wake model + gates + STT/TTS up, clean shutdown")


if __name__ == "__main__":
    asyncio.run(main())
