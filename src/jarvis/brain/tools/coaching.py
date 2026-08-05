"""Coaching tools (Phase 2 companion) — Watari as a coach for the fields you focus on.

The owner asked for e.g. an evening German check at his current level, with progress tracked over
time. The QUIZ is conducted conversationally by the agent (it's good at that); these tools give it
the state it needs and let it record the outcome:

  * ``start_skill_check`` — begin a review: returns the current level + an instruction for the agent
    to run a short, level-appropriate check one question at a time, then call record_skill_review;
  * ``record_skill_review`` — log a 1-10 score + note (updates streak/trend);
  * ``skill_progress`` — report level, recent average, trend, streak;
  * ``set_skill_level`` — set/correct the level;
  * ``list_skills`` — the fields being coached.

All local + persisted (brain/coaching.py). Degrade to plain notes; never raise.
"""

from __future__ import annotations

from jarvis.brain.coaching import COACH
from jarvis.brain.tools.base import tool_error

# Per-field hints for how the agent should run the review. Unknown fields get a generic directive.
_REVIEW_HINTS: dict[str, str] = {
    "german": ("Run a short GERMAN check at level {level}: ask 3 things one at a time — a vocab/"
               "translation item, a quick grammar/gap-fill, and a prompt for him to answer in a full "
               "German sentence. Keep it conversational, correct gently."),
    "french": ("Run a short FRENCH check at level {level}: 3 items one at a time (vocab, a gap-fill, "
               "and a prompt to reply in a full French sentence). Correct gently."),
    "spanish": ("Run a short SPANISH check at level {level}: 3 items one at a time (vocab, a gap-fill, "
                "and a prompt to reply in a full Spanish sentence). Correct gently."),
}


def _directive(field: str, level: str) -> str:
    tmpl = _REVIEW_HINTS.get(field, (
        "Run a short review of '{field}' at level {level}: ask 3 focused questions one at a time to "
        "gauge where he's at, then give brief feedback."))
    return tmpl.format(field=field, level=level)


async def start_skill_check(args: dict) -> str:
    field = (args.get("field") or "").strip().lower()
    if not field:
        fields = COACH.fields()
        return (f"Which field, sir? I'm tracking: {', '.join(fields)}." if fields
                else "Which field should we review, sir?")
    try:
        p = COACH.progress(field)
        level = p.level if p.level != "not set" else "unknown — ask him to gauge it"
        streak = f" You're on a {p.streak}-day streak." if p.streak > 1 else ""
        directive = _directive(field, level)
        # The agent reads this and conducts the check, then calls record_skill_review.
        return (f"Starting a {field} review, sir (level {p.level}).{streak} {directive} "
                f"When you're done, score him 1-10 and call record_skill_review('{field}', score, note).")
    except Exception as e:  # noqa: BLE001
        return tool_error("skill check", e)


async def record_skill_review(args: dict) -> str:
    field = (args.get("field") or "").strip().lower()
    if not field:
        return "Which field was that, sir?"
    try:
        score = int(args.get("score"))
    except (TypeError, ValueError):
        return "What score out of 10, sir?"
    note = (args.get("note") or "").strip()
    try:
        p = COACH.record_review(field, score, note)
        streak = f" {p.streak}-day streak" if p.streak > 1 else ""
        tail = f" — trend: {p.trend}" if p.trend != "new" else ""
        return f"Logged, sir: {field} {score}/10 (level {p.level}).{streak}{tail}."
    except Exception as e:  # noqa: BLE001
        return tool_error("record review", e)


async def skill_progress(args: dict) -> str:
    field = (args.get("field") or "").strip().lower()
    if not field:
        fields = COACH.fields()
        return f"Which field, sir? I track: {', '.join(fields)}." if fields else "No fields tracked yet, sir."
    try:
        p = COACH.progress(field)
        if p.reviews == 0:
            return (f"No {field} reviews logged yet, sir (level {p.level}). "
                    "Say 'quiz me on {0}' and we'll start.".format(field))
        avg = f"recent average {p.recent_avg}/10" if p.recent_avg is not None else ""
        streak = f", {p.streak}-day streak" if p.streak > 1 else ""
        return (f"{field.title()}: level {p.level}, {p.reviews} review(s), {avg}, trend {p.trend}"
                f"{streak}, sir.")
    except Exception as e:  # noqa: BLE001
        return tool_error("skill progress", e)


async def set_skill_level(args: dict) -> str:
    field = (args.get("field") or "").strip().lower()
    level = (args.get("level") or "").strip()
    if not (field and level):
        return "Which field and what level, sir? (e.g. German to B1.)"
    try:
        COACH.set_level(field, level)
        return f"Noted, sir — {field} is at {level}. I'll pitch your reviews there."
    except Exception as e:  # noqa: BLE001
        return tool_error("set level", e)


async def list_coaching(_args: dict) -> str:
    try:
        fields = COACH.fields()
        if not fields:
            return "I'm not coaching any fields yet, sir."
        bits = []
        for f in fields:
            p = COACH.progress(f)
            bits.append(f"{f} (level {p.level}" + (f", {p.streak}-day streak" if p.streak > 1 else "") + ")")
        return "I'm coaching, sir: " + "; ".join(bits) + "."
    except Exception as e:  # noqa: BLE001
        return tool_error("list skills", e)


SCHEMAS = [
    {"type": "function", "function": {
        "name": "start_skill_check",
        "description": ("Begin a coaching review/quiz for one of the owner's focus fields (e.g. "
                        "German) at his current level. Returns his level + how to run it; then YOU "
                        "conduct a few level-appropriate questions conversationally, one at a time, "
                        "and finish by calling record_skill_review. Use for 'quiz me on X', 'test my "
                        "German', 'let's practise X'."),
        "parameters": {"type": "object", "properties": {
            "field": {"type": "string", "description": "The field, e.g. 'german'."}},
            "required": ["field"]}}},
    {"type": "function", "function": {
        "name": "record_skill_review",
        "description": ("Record the result of a skill review: a 1-10 score and a short note on how it "
                        "went. Call this at the END of a quiz/practice you ran. Updates streak + trend."),
        "parameters": {"type": "object", "properties": {
            "field": {"type": "string", "description": "The field, e.g. 'german'."},
            "score": {"type": "integer", "description": "How he did, 1-10."},
            "note": {"type": "string", "description": "Brief note (strengths/what to work on)."}},
            "required": ["field", "score"]}}},
    {"type": "function", "function": {
        "name": "skill_progress",
        "description": ("Report the owner's progress in a coaching field: level, recent average score, "
                        "trend, streak. Use for 'how's my German?', 'am I improving at X?'."),
        "parameters": {"type": "object", "properties": {
            "field": {"type": "string", "description": "The field, e.g. 'german'."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "set_skill_level",
        "description": ("Set or correct the owner's level in a coaching field (e.g. German -> B1). Use "
                        "for 'I'm B1 in German now', 'set my Spanish to beginner'."),
        "parameters": {"type": "object", "properties": {
            "field": {"type": "string", "description": "The field."},
            "level": {"type": "string", "description": "The level, free-form (A2/B1/intermediate/…)."}},
            "required": ["field", "level"]}}},
    {"type": "function", "function": {
        "name": "list_coaching",
        "description": "List the fields Watari is actively coaching, with each one's level and "
                       "current streak. Use for 'what are you coaching me on', 'how's my training "
                       "going'. For one field in depth, use skill_progress.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
]

HANDLERS = {
    "start_skill_check": start_skill_check,
    "record_skill_review": record_skill_review,
    "skill_progress": skill_progress,
    "set_skill_level": set_skill_level,
    "list_coaching": list_coaching,
}
