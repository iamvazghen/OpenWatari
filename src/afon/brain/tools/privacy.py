"""What Afon stores about the owner, asked out loud (37.F1).

A lazy group on purpose. "What do you know about me", "what are you storing", "how long do you keep
that" are unmistakable phrases, asked rarely, and the per-turn tool surface is a budget the streaming
prefill pays on every single turn. The inventory itself lives in `brain/inventory.py`; this is only
the way to ask it.
"""

from __future__ import annotations

from afon.brain.tools.base import tool_error


async def what_you_know(_args: dict) -> str:
    try:
        from afon.brain.inventory import spoken

        return spoken()
    except Exception as e:  # noqa: BLE001
        return tool_error("data inventory", e)


async def data_retention(_args: dict) -> str:
    """The long form — every store, one line each. For reading rather than hearing."""
    try:
        from afon.brain.inventory import report

        return report()
    except Exception as e:  # noqa: BLE001
        return tool_error("retention report", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "what_you_know",
            "description": (
                "Report every store Afon keeps about the owner — how many, how large, which expire "
                "and which are kept indefinitely, and which never leave this laptop. Use for 'what "
                "do you know about me?', 'what are you storing?', 'where does my data live?'. "
                "Generated from the code, so it cannot go stale."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "data_retention",
            "description": (
                "The full store-by-store table: what each holds and how long it is kept. Use when "
                "the owner asks about ONE store or wants the detail behind what_you_know — 'how "
                "long do you keep my audit log?', 'list everything you store'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

HANDLERS = {"what_you_know": what_you_know, "data_retention": data_retention}
