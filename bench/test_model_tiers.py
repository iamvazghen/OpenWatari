"""Compare TTFT across candidate models to pick a fast conversational tier."""
import asyncio
import time

from openai import AsyncOpenAI

from afon.config import settings

PROMPT = "In one short sentence, what's the capital of France?"


async def ttft(client, model):
    t0 = time.perf_counter()
    first = None
    out = []
    try:
        s = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": PROMPT}],
            stream=True,
            temperature=0.4,
        )
        async for ch in s:
            d = ch.choices[0].delta.content if ch.choices else None
            if d:
                if first is None:
                    first = time.perf_counter() - t0
                out.append(d)
        return first, time.perf_counter() - t0, "".join(out).strip()
    except Exception as e:
        return None, None, f"ERR {type(e).__name__}: {e}"


async def main():
    c = AsyncOpenAI(
        base_url=settings.freellmapi_base_url,
        api_key=settings.freellmapi_api_key or "x",
        timeout=30,
        max_retries=0,
    )
    for m in ["llama-3.1-8b-instant", "llama-3.3-70b-versatile", "mistral-small-latest"]:
        f, t, txt = await ttft(c, m)
        fs = f"{f*1000:.0f}ms" if f else "n/a"
        ts = f"{t*1000:.0f}ms" if t else "n/a"
        print(f"{m:32} TTFT={fs:>8}  total={ts:>8}  | {txt[:60]}")


if __name__ == "__main__":
    asyncio.run(main())
