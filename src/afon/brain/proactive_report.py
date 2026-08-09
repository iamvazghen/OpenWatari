"""Proactive action reports — when Afon does something UNPROMPTED, he says so, and explains why.

The autonomous drivers (backlog pass, objectives advance, …) take real actions on the owner's behalf —
e.g. commenting drafted work onto his Notion tasks. Silently is not enough: the owner asked to be told
WHAT was done, WHY (the trigger), and Afon's brief reasoning/chain of thought. This turns a batch of
actions into one short spoken report, delivered through the normal proactive path (listening edge →
Telegram voice note → ntfy push) by the caller.

One bounded LLM call produces the chain-of-thought prose from the REAL actions (no invention); a
deterministic fallback keeps the report honest and never-silent if the model is unavailable. ``llm`` is
injectable so the logic is testable without a real model.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


def _deterministic_report(actions: list[dict], trigger: str) -> str:
    """Honest, model-free report: what + why + where to look. Never invents reasoning."""
    n = len(actions)
    titles = "; ".join((a.get("title") or "a task") for a in actions)
    what = "a task" if n == 1 else f"{n} tasks"
    return (f"Sir, while you were away I worked ahead on {what} — {trigger}. "
            f"I made progress on: {titles}. The details are in each task's Notion comments.")


async def spoken_action_report(
    llm: Any,
    actions: list[dict],
    *,
    trigger: str,
) -> str:
    """A short spoken report of AUTONOMOUS actions: what was done, why, and the reasoning.
    ``actions`` = ``[{title, result, ...}]``. Returns '' only when there were no actions."""
    actions = [a for a in (actions or []) if a]
    if not actions:
        return ""
    fallback = _deterministic_report(actions, trigger)
    if llm is None:
        return fallback
    bullets = "\n".join(
        f"- {(a.get('title') or 'a task')}: {((a.get('result') or '').strip()[:400] or 'progress made')}"
        for a in actions
    )
    messages = [
        {"role": "system", "content": (
            "You are Afon, reporting to your owner (address him as 'sir'). You just did the work below "
            "AUTONOMOUSLY and unprompted, triggered by: " + trigger + ". In 2-4 short sentences, spoken "
            "aloud (first person, no lists or markdown), tell him WHAT you did, WHY (the trigger), and "
            "your brief reasoning — your chain of thought — for each. Mention the details are in the "
            "Notion comments. Be specific and honest; do NOT invent actions beyond those listed.")},
        {"role": "user", "content": f"Actions I took:\n{bullets}"},
    ]
    try:
        msg = await llm.complete(messages)
        text = (getattr(msg, "content", "") or "").strip()
        return text or fallback
    except Exception as e:  # noqa: BLE001 — a report must never crash the autonomous pass
        logger.warning(f"action report LLM failed ({type(e).__name__}); using deterministic report")
        return fallback


if __name__ == "__main__":
    import asyncio

    class _FakeMsg:
        content = "Sir, I drafted an outline for your Q3 review because it was overdue; my thinking was to unblock it. Details are in the Notion comments."

    class _FakeLLM:
        async def complete(self, messages, **kw):
            assert "AUTONOMOUSLY" in messages[0]["content"]  # the reasoning instruction reached the model
            return _FakeMsg()

    class _DeadLLM:
        async def complete(self, *a, **k):
            raise RuntimeError("no credits")

    acts = [{"title": "Q3 review", "result": "Drafted an outline.", "commented": True}]
    r = asyncio.run(spoken_action_report(_FakeLLM(), acts, trigger="it was overdue"))
    assert "Notion" in r and "sir" in r.lower(), r
    assert asyncio.run(spoken_action_report(None, acts, trigger="it was overdue")) == \
        _deterministic_report(acts, "it was overdue")
    # LLM failure -> deterministic fallback, never silent.
    assert asyncio.run(spoken_action_report(_DeadLLM(), acts, trigger="it was overdue")) == \
        _deterministic_report(acts, "it was overdue")
    assert asyncio.run(spoken_action_report(_FakeLLM(), [], trigger="x")) == ""  # nothing done -> silent
    print("proactive_report self-check OK")
