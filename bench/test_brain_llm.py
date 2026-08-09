"""Smoke-test the brain LLMClient against freellmapi: streaming + tool-calling + failover."""
import asyncio
import time
from afon.brain.llm import LLMClient


async def main():
    c = LLMClient()
    print("chain:", c.chain)

    # 1) streaming
    print("\n[stream] 'say hello as Afon in one short sentence':")
    t0 = time.perf_counter()
    first = None
    out = []
    async for d in c.stream([{"role": "user", "content": "Say hello as Afon in one short sentence."}]):
        if first is None:
            first = time.perf_counter() - t0
        out.append(d)
    print("  ->", "".join(out).strip())
    print(f"  TTFT={first*1000:.0f}ms total={(time.perf_counter()-t0)*1000:.0f}ms")

    # 2) tool-calling
    tools = [{
        "type": "function",
        "function": {
            "name": "get_time",
            "description": "Get the current local time.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }]
    print("\n[tools] 'what time is it?' -> expect a tool_call:")
    msg = await c.complete(
        [{"role": "user", "content": "What time is it? Use the get_time tool."}], tools=tools
    )
    tc = getattr(msg, "tool_calls", None)
    print("  tool_calls:", [(t.function.name, t.function.arguments) for t in tc] if tc else None)
    print("  content:", (msg.content or "").strip()[:120])


if __name__ == "__main__":
    asyncio.run(main())
