"""Benchmark freellmapi models for Afon's brain.

For a voice agent, Time-To-First-Token (TTFT) dominates perceived latency, so we
stream and measure when the first token arrives, plus total time and whether the
answer is correct/non-empty. Run: python bench/llm_bench.py
"""

from __future__ import annotations

import os
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    base_url=os.environ["AFON_FREELLMAPI_BASE_URL"],
    api_key=os.environ["AFON_FREELLMAPI_API_KEY"],
)

CANDIDATES = [
    "auto",
    "llama-3.1-8b-instant",            # the old project's primary — does it still exist?
    "llama-3.3-70b-versatile",
    "gemini-3-flash-preview",
    "gemini-3.5-flash",
    "groq/compound",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b:free",
    "z-ai/glm-4.5-air:free",
    "mistral-small-latest",
    "meta-llama/llama-3.3-70b-instruct:free",
]

SYSTEM = "You are Afon, a concise voice assistant. Answer in one short sentence."
USER = "What is the capital of Armenia?"


def bench(model: str) -> dict:
    t0 = time.perf_counter()
    ttft = None
    text = ""
    try:
        stream = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}],
            max_tokens=40,
            temperature=0.3,
            stream=True,
            timeout=25,
        )
        for chunk in stream:
            delta = (chunk.choices[0].delta.content or "") if chunk.choices else ""
            if delta and ttft is None:
                ttft = time.perf_counter() - t0
            text += delta
        total = time.perf_counter() - t0
        ok = "erevan" in text.lower() or "yerevan" in text.lower()
        return {"model": model, "ttft": ttft, "total": total, "ok": ok,
                "reply": text.strip().replace("\n", " ")[:60]}
    except Exception as e:
        return {"model": model, "ttft": None, "total": time.perf_counter() - t0,
                "ok": False, "reply": f"ERR {type(e).__name__}: {str(e)[:50]}"}


def main() -> None:
    rows = [bench(m) for m in CANDIDATES]
    rows.sort(key=lambda r: (r["ttft"] is None, r["ttft"] or 9e9))
    print(f"\n{'model':<42}{'TTFT':>8}{'total':>8}  ok  reply")
    print("-" * 100)
    for r in rows:
        ttft = f"{r['ttft']*1000:.0f}ms" if r["ttft"] else "  -  "
        total = f"{r['total']*1000:.0f}ms"
        print(f"{r['model']:<42}{ttft:>8}{total:>8}  {'Y' if r['ok'] else 'n'}   {r['reply']}")


if __name__ == "__main__":
    main()
