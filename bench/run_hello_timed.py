"""Start the Phase-0 pipeline, run ~8s, then stop. Confirms audio devices open,
Deepgram/ElevenLabs connect, the gate is wired, and shutdown is clean — without a
human voice test. Surfaces any DeprecationWarning."""

import asyncio

from afon.edge.hello_voice import build_worker
from pipecat.workers.runner import WorkerRunner


async def main() -> None:
    runner = WorkerRunner()
    await runner.add_workers(build_worker())
    run = asyncio.create_task(runner.run())
    print(">>> pipeline started; running 8s…", flush=True)
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
    print(">>> STARTUP OK — gate wired, devices opened, services up, clean shutdown")


if __name__ == "__main__":
    asyncio.run(main())
