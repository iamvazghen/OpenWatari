"""Portfolio tools (S40) — read the owner's ledger, value it, and move nothing.

**One tool, and it is a read.** 40.F2 asks that the absence of a transaction capability be
asserted rather than merely true, so this module is kept deliberately small and obvious: there is
no client, no credential, no parameter that takes an amount or a destination, and no second
function. `test_portfolio.py` holds the whole finance family to that.
"""

from __future__ import annotations

from afon.brain.tools.base import tool_error


async def portfolio_snapshot(_args: dict) -> str:
    try:
        from afon.brain.portfolio import snapshot

        return (await snapshot()).spoken()
    except Exception as e:  # noqa: BLE001
        return tool_error("portfolio", e)


SCHEMAS = [
    {"type": "function", "function": {
        "name": "portfolio_snapshot",
        "description": "What the owner holds, valued from his own plain-text ledger. Use for "
                       "'what do I own', 'how's the portfolio', 'what's it all worth'. Read-only: "
                       "you cannot buy, sell or move anything, and you should say so plainly if "
                       "asked to. Every value carries where the price came from and when it was "
                       "true — relay that, especially when a price is old.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
]

HANDLERS = {"portfolio_snapshot": portfolio_snapshot}
