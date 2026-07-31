"""Wolfram Alpha — exact computation + curated facts (the 'never guess numbers' tool).

One tool: ``compute``. Uses the LLM API endpoint (plain-text answers sized for a model to relay).
Degrades to a spoken note without JARVIS_WOLFRAM_APP_ID.
"""

from __future__ import annotations

from jarvis.config import settings
from jarvis.brain.tools.base import clip, http_get, not_configured, tool_error

_NEEDS = "a Wolfram Alpha AppID (JARVIS_WOLFRAM_APP_ID — free tier at developer.wolframalpha.com)"


def _configured() -> bool:
    return bool(settings.wolfram_app_id)


async def compute(args: dict) -> str:
    if not _configured():
        return not_configured("Wolfram Alpha", _NEEDS)
    query = (args.get("query") or "").strip()
    if not query:
        return "What should I compute, sir?"
    try:
        r = await http_get("https://www.wolframalpha.com/api/v1/llm-api",
                           params={"input": query, "appid": settings.wolfram_app_id,
                                   "maxchars": 1200})
        return clip(r.text, 1500) or "Wolfram had no result for that, sir."
    except Exception as e:  # noqa: BLE001
        if "501" in str(e):
            return f"Wolfram couldn't interpret '{query}', sir — try rephrasing it."
        return tool_error("computation", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "compute",
            "description": (
                "EXACT math, unit conversion, dates, physics, finance formulas and curated facts via "
                "Wolfram Alpha — use instead of guessing any non-trivial number ('compound interest "
                "on 5k at 4% for 10 years', 'convert 180 lbs to kg', 'sunset time in Berlin today')."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "The computation/question."}},
                "required": ["query"],
            },
        },
    },
]

HANDLERS = {"compute": compute}
