"""02.R1 — what a turn costs, broken down by the intent class that caused it.

`turn_trace` already records what every turn was charged for: the catalogue it presented, the
prefill it sent, and (since this task) the model that answered and the tokens that came back. What
it could not say is which KIND of turn spends the money, and that is the only breakdown that
changes a decision: "chatter costs 7,300 tokens a turn" is a fact you can act on by not advertising
tools to chatter, while "the brain spent 4M tokens this week" is a fact you can only wince at.

Two rules hold this honest:

* **An unpriced model is never counted as free.** Most of the chain is free today (the freellmapi
  proxy, Groq's free tier), and a zero for "we don't know" mixed with a zero for "it cost nothing"
  makes the total a number nobody can trust. Unpriced turns are counted separately and reported.
* **Prices are declared here, not fetched.** A voice assistant that calls a pricing API to render a
  HUD line has bought an outage. These are list prices in USD per million tokens, dated, and wrong
  the moment a provider changes them — which is why the HUD says "est." and the row says when the
  table was last checked.

ponytail: no currency library, no per-request billing records. Dollars are a float rounded at the
edge, because the number this produces is a weekly-scale estimate for one person.

    uv run python -m afon.brain.cost
"""
from __future__ import annotations

#: When the table below was last checked against the providers' own pages. Printed in the HUD, so
#: a stale estimate is visibly stale rather than quietly wrong.
PRICED_ON = "2026-09-14"

#: model prefix -> (USD per 1M input tokens, USD per 1M output tokens).
#: Matched by PREFIX against the chain entry (``minimax:MiniMax-Text-01``), so a provider's whole
#: family can share a row and an unknown model falls through to None rather than to zero.
PRICES: dict[str, tuple[float, float]] = {
    "minimax:": (0.30, 1.20),
    "groq:llama-3.3-70b": (0.59, 0.79),
    "groq:": (0.0, 0.0),                 # the free tier, deliberately priced rather than unknown
    "freellmapi:": (0.0, 0.0),           # the local proxy: no per-token charge
    "ollama:": (0.0, 0.0),               # runs on the owner's own hardware
    "gemini-3.5-flash": (0.075, 0.30),
}


def price_of(model: str) -> tuple[float, float] | None:
    """($/Mtok in, $/Mtok out) for a chain entry, or None when the table has never met it."""
    m = (model or "").strip()
    if not m:
        return None
    # Longest prefix wins, so "groq:llama-3.3-70b" beats the "groq:" free-tier row.
    best = max((k for k in PRICES if m.startswith(k)), key=len, default=None)
    return PRICES[best] if best else None


def usd(model: str, in_tokens: int, out_tokens: int) -> float | None:
    """Cost of one call, or None if the model is unpriced. None is never silently a zero."""
    p = price_of(model)
    if p is None:
        return None
    return (in_tokens / 1e6) * p[0] + (out_tokens / 1e6) * p[1]


def by_intent(rows: list[dict]) -> dict[str, dict]:
    """Per-intent-class totals over trace rows: turns, tokens, and estimated money.

    `unpriced` counts the turns whose model the table does not know, so a small `usd` next to a
    large `unpriced` reads as "we cannot see most of this" rather than "it was cheap".
    """
    out: dict[str, dict] = {}
    for r in rows or []:
        cls = str(r.get("intent") or "general")
        agg = out.setdefault(cls, {"turns": 0, "prefill_tokens": 0, "catalogue_tokens": 0,
                                   "answer_tokens": 0, "usd": 0.0, "unpriced": 0})
        agg["turns"] += 1
        agg["prefill_tokens"] += int(r.get("prefill_tokens") or 0)
        agg["catalogue_tokens"] += int(r.get("catalogue_tokens") or 0)
        agg["answer_tokens"] += int(r.get("answer_tokens") or 0)
        money = usd(str(r.get("model") or ""), int(r.get("prefill_tokens") or 0),
                    int(r.get("answer_tokens") or 0))
        if money is None:
            agg["unpriced"] += 1
        else:
            agg["usd"] += money
    for agg in out.values():
        agg["usd"] = round(agg["usd"], 4)
        agg["usd_per_turn"] = round(agg["usd"] / agg["turns"], 5) if agg["turns"] else 0.0
        agg["prefill_per_turn"] = round(agg["prefill_tokens"] / agg["turns"]) if agg["turns"] else 0
    return dict(sorted(out.items(), key=lambda kv: -kv[1]["prefill_tokens"]))


def _selfcheck() -> None:
    """ponytail: the one runnable check — unknown is not free, and the split adds up."""
    assert price_of("minimax:MiniMax-Text-01") == (0.30, 1.20)
    assert price_of("groq:llama-3.3-70b-versatile") == (0.59, 0.79), "longest prefix must win"
    assert price_of("groq:whatever-else") == (0.0, 0.0)
    assert price_of("some-model-nobody-priced") is None
    assert usd("some-model-nobody-priced", 1000, 1000) is None, "unknown is NOT free"
    assert abs(usd("minimax:x", 1_000_000, 0) - 0.30) < 1e-9

    rows = [
        {"intent": "chat", "prefill_tokens": 700, "catalogue_tokens": 0, "answer_tokens": 40,
         "model": "minimax:MiniMax-Text-01"},
        {"intent": "act", "prefill_tokens": 9000, "catalogue_tokens": 7300, "answer_tokens": 60,
         "model": "minimax:MiniMax-Text-01"},
        {"intent": "act", "prefill_tokens": 9000, "catalogue_tokens": 7300, "answer_tokens": 60,
         "model": "mystery-model"},
    ]
    agg = by_intent(rows)
    assert agg["act"]["turns"] == 2 and agg["act"]["unpriced"] == 1
    assert agg["act"]["usd"] > 0 and agg["chat"]["usd"] > 0
    assert list(agg) == ["act", "chat"], "heaviest class first — the HUD reads top-down"
    print(f"selfcheck ok — {len(PRICES)} priced model families, table checked {PRICED_ON}")
    for cls, a in agg.items():
        print(f"  {cls:<9} {a['turns']} turns  {a['prefill_per_turn']} prefill/turn  "
              f"${a['usd']:.4f} ({a['unpriced']} unpriced)")


if __name__ == "__main__":
    _selfcheck()
