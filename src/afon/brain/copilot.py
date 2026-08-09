"""Task co-pilot (Phase 3) — Afon plans a task WITH the owner, then executes it for him.

The to-do tools (tools/tasks.py) let the owner capture and track tasks. This module is the next
step up: turning a captured task into WORK. The loop the owner asked for is

    clarify  ->  plan  ->  confirm  ->  execute (worker / fleet)  ->  report

  * **clarify + plan** (``build_plan``): a single bounded LLM call turns a task title+description
    into a concrete plan — clarifying questions (only if it's genuinely underspecified), an ordered
    list of steps, which of those steps are OUTWARD (need the owner's approval), and a recommendation
    of who should do it: Afon himself (the bounded worker) or the OpenClaw fleet (deep domain work).
    This is the "dry-run": it shows the plan without doing anything.
  * **confirm**: the plan is spoken back and the owner says go (or answers the questions first).
  * **execute**: handed to the ``TaskWorker`` (safe research/draft/code, DEFERS every outward step)
    or delegated to the fleet — in the BACKGROUND, linked back to the to-do so its progress updates.
  * **report**: completion is announced by voice via the TaskQueue, as with any background job.

The defer-outward invariant stays sacred: execution never sends/deletes/pushes unattended — those
steps are always listed back for approval. This module is pure logic (an injected ``llm``), so the
whole plan/parse/executor-choice path is hermetically testable with a fake LLM.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

# Domains that are genuinely better handled by the fleet's specialists (ispir routes them) than by
# Afon's own bounded worker. Used to sanity-check / default the executor recommendation.
_FLEET_DOMAINS = {
    "finance/markets": ("stock", "etf", "market", "portfolio", "invest", "dividend", "bond",
                        "earnings", "valuation"),
    "real estate": ("apartment", "house", "rent", "is24", "immobilien", "property", "listing",
                    "mortgage"),
    "crypto": ("crypto", "bitcoin", "btc", "eth", "wallet", "token", "defi", "on-chain"),
    "deep research": ("deep research", "market research", "competitive analysis", "due diligence",
                      "landscape", "thorough report"),
}

_MAX_STEPS = 6
_MAX_QUESTIONS = 3


@dataclass
class Plan:
    """A dry-run plan for a task: what to ask, what to do, who does it, what needs approval."""

    steps: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)   # clarifying Qs (empty if the task is clear)
    outward: list[str] = field(default_factory=list)     # steps that will need the owner's go-ahead
    executor: str = "self"                                # "self" (worker) | "fleet"
    summary: str = ""

    @property
    def needs_clarification(self) -> bool:
        return bool(self.questions)


def _heuristic_executor(text: str) -> str:
    """Pick 'fleet' for deep-domain work, else 'self' — a floor under the model's recommendation."""
    t = (text or "").lower()
    for _domain, words in _FLEET_DOMAINS.items():
        if any(w in t for w in words):
            return "fleet"
    return "self"


def _coerce_list(value: Any, cap: int) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        s = str(item).strip()
        if s:
            out.append(s[:200])
        if len(out) >= cap:
            break
    return out


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of an LLM reply (tolerates prose/fences around it)."""
    if not text:
        return None
    start = text.find("{")
    if start < 0:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        # A ```json fence or trailing junk — retry from the last brace-balanced slice.
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


_PLAN_SYSTEM = (
    "You are Afon, planning a task for the owner before doing it. Think it through, then reply with "
    "ONLY a JSON object (no prose, no code fence) with these keys:\n"
    '  "questions": array of up to 3 SHORT clarifying questions — ONLY if the task is genuinely too '
    "underspecified to start; empty array if it's clear enough to plan.\n"
    '  "steps": array of up to 6 concrete, ordered steps you would take.\n'
    '  "outward": array of any steps that SEND, POST, DELETE, PAY, PUSH, or otherwise affect the '
    "outside world / the owner's accounts (these will need his explicit approval); empty if none.\n"
    '  "executor": "fleet" if this needs deep domain expertise (finance, markets, crypto, real-estate, '
    'large multi-step research, big coding jobs), otherwise "self".\n'
    '  "summary": one short sentence describing the plan.\n'
    "Be realistic and concise. Do NOT include any step you cannot actually do with tools."
)


async def build_plan(
    title: str,
    description: str,
    llm: Any,
    *,
    fleet_available: bool = False,
) -> Plan:
    """Produce a :class:`Plan` for a task via one bounded LLM call. Fail-quiet to a minimal plan.

    ``fleet_available`` clamps the recommendation: if the fleet isn't authorized this session we never
    recommend it (the caller would only have to fall back). The whole thing degrades to a sensible
    one-step self-plan if the model is unreachable or returns junk — planning must never raise.
    """
    title = (title or "").strip()
    desc = (description or "").strip()
    brief = title if not desc else f"{title}\nDetails: {desc}"
    fallback = Plan(
        steps=[f"Work through '{title}' step by step and produce a result."] if title else [],
        executor=_heuristic_executor(brief) if fleet_available else "self",
        summary=f"Get '{title}' done." if title else "",
    )
    if not title:
        return fallback
    try:
        msg = await llm.complete([
            {"role": "system", "content": _PLAN_SYSTEM},
            {"role": "user", "content": f"Task: {brief}"},
        ])
        data = _extract_json(getattr(msg, "content", "") or "")
    except Exception as e:  # noqa: BLE001 — a planning hiccup must never fail the turn
        logger.warning(f"copilot build_plan failed: {type(e).__name__}: {e}")
        return fallback
    if not data:
        return fallback

    plan = Plan(
        steps=_coerce_list(data.get("steps"), _MAX_STEPS) or fallback.steps,
        questions=_coerce_list(data.get("questions"), _MAX_QUESTIONS),
        outward=_coerce_list(data.get("outward"), _MAX_STEPS),
        summary=str(data.get("summary") or fallback.summary).strip()[:300],
    )
    executor = str(data.get("executor") or "").strip().lower()
    if executor not in ("self", "fleet"):
        executor = _heuristic_executor(brief)
    # Never recommend the fleet when it isn't available — the caller would just downgrade anyway.
    plan.executor = "self" if (executor == "fleet" and not fleet_available) else executor
    return plan


# ---- (de)serialisation onto a to-do's meta ------------------------------------------------

def plan_to_meta(plan: Plan) -> dict:
    return {
        "steps": plan.steps,
        "questions": plan.questions,
        "outward": plan.outward,
        "executor": plan.executor,
        "summary": plan.summary,
    }


def plan_from_meta(meta: dict | None) -> Plan | None:
    if not isinstance(meta, dict):
        return None
    if not (meta.get("steps") or meta.get("summary")):
        return None
    return Plan(
        steps=list(meta.get("steps") or []),
        questions=list(meta.get("questions") or []),
        outward=list(meta.get("outward") or []),
        executor=str(meta.get("executor") or "self"),
        summary=str(meta.get("summary") or ""),
    )


# ---- spoken renderings --------------------------------------------------------------------

def _ordinal_steps(steps: list[str]) -> str:
    return " ".join(f"{i}) {s.rstrip('.')}." for i, s in enumerate(steps, 1))


def spoken_plan(plan: Plan, title: str) -> str:
    """The plan read back to the owner — the confirm step of the loop."""
    who = "hand it to the fleet" if plan.executor == "fleet" else "handle it myself"
    parts: list[str] = []
    if plan.summary:
        parts.append(f"Here's my plan for '{title}', sir: {plan.summary.rstrip('.')}.")
    else:
        parts.append(f"Here's my plan for '{title}', sir.")
    if plan.steps:
        parts.append(_ordinal_steps(plan.steps))
    parts.append(f"I'd {who}.")
    if plan.outward:
        parts.append("These steps will need your go-ahead first: " + "; ".join(plan.outward) + ".")
    if plan.questions:
        parts.append("First, though — " + " ".join(q.rstrip("?") + "?" for q in plan.questions))
        parts.append("Answer those, or say 'just proceed' and I'll get on it.")
    else:
        parts.append("Shall I proceed, sir?")
    return " ".join(parts)


def execution_objective(title: str, description: str, plan: Plan | None) -> str:
    """The brief handed to the worker/fleet — the task plus its agreed plan, if any."""
    lines = [f"Task: {title}"]
    if description:
        lines.append(f"Details: {description}")
    if plan and plan.steps:
        lines.append("Agreed plan:")
        lines.extend(f"  {i}. {s}" for i, s in enumerate(plan.steps, 1))
    if plan and plan.outward:
        lines.append("Do NOT perform these outward steps yourself — note them for the owner's "
                     "approval instead: " + "; ".join(plan.outward) + ".")
    lines.append("Do the safe work now and report a concise result.")
    return "\n".join(lines)
