"""Inventory tools (S47) — what you have, and when it runs out.

Two tools, not four. Adding and using are the same act with a sign, and a separate `delete` would
be a third way to reach zero; setting the change to the negative of what is there does it.

Not confirm-gated, deliberately and for the same reason task capture is not: an inventory only
works if recording a thing costs less than remembering it. Nothing here is destructive — the event
log keeps every change, so a wrong number is corrected by another number rather than lost.
"""

from __future__ import annotations

from afon.brain import stock
from afon.brain.tools.base import tool_error


async def check_stock(args: dict) -> str:
    try:
        return stock.spoken((args.get("item") or "").strip(),
                            category=(args.get("category") or "").strip())
    except Exception as e:  # noqa: BLE001
        return tool_error("inventory", e)


async def update_stock(args: dict) -> str:
    try:
        name = (args.get("item") or "").strip()
        if not name:
            return "Which item, sir?"
        try:
            change = float(args.get("change"))
        except (TypeError, ValueError):
            return "How many, sir? Give me a number — positive to add, negative to use."
        if change >= 0:
            item = stock.add(name, change, unit=(args.get("unit") or "").strip(),
                             category=(args.get("category") or "").strip(),
                             location=(args.get("location") or "").strip())
            if item is None:
                return f"I couldn't record that, sir."
            return f"Noted, sir — {item.said()}."
        item = stock.consume(name, -change)
        if item is None:
            # 47.F3. Consuming something unrecorded would have to invent what was there before.
            return (f"I've nothing recorded for '{name}', sir, so I can't take any off. Tell me "
                    "what you have and I'll count down from there.")
        days, why = stock.depletion(name)
        tail = f" About {days:.0f} days left at that rate." if days is not None else ""
        return f"Noted, sir — {item.said()}.{tail}"
    except Exception as e:  # noqa: BLE001
        return tool_error("inventory", e)


SCHEMAS = [
    {"type": "function", "function": {
        "name": "check_stock",
        "description": "How much of something the owner has, and how long it will last at the rate "
                       "it has been used. Use for 'how much feed do we have', 'am I low on X', "
                       "'what are you tracking'. An item never recorded comes back as UNKNOWN, "
                       "which is not the same as none — say that difference out loud.",
        "parameters": {"type": "object", "properties": {
            "item": {"type": "string", "description": "The item. Omit to list everything."},
            "category": {"type": "string", "description": "Optional: only this category."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "update_stock",
        "description": "Record stock arriving or being used. `change` is positive to add ('forty "
                       "more sacks came in') and negative to use ('we used five'). Using something "
                       "never recorded is refused rather than guessed at.",
        "parameters": {"type": "object", "properties": {
            "item": {"type": "string", "description": "The item."},
            "change": {"type": "number", "description": "Positive to add, negative to use."},
            "unit": {"type": "string", "description": "sacks, litres, boxes — set once."},
            "category": {"type": "string", "description": "Which group it belongs to."},
            "location": {"type": "string", "description": "Where it is kept."}},
            "required": ["item", "change"]}}},
]

HANDLERS = {"check_stock": check_stock, "update_stock": update_stock}
