"""Skills — on-demand know-how Jarvis can read when he needs it (Phase 13).

Skills are Markdown playbooks under ``skills/`` (coding discipline, his own architecture, the
self-improvement workflow, language notes…). They are **not** injected into the system prompt — that
would bloat every turn — so instead Jarvis pulls the relevant one with ``read_skill`` exactly when a
task calls for it, the way a developer opens the right doc. ``list_skills`` shows what's available.

Drop a new ``skills/<name>.md`` and it's instantly available — no code change.
"""

from __future__ import annotations

from pathlib import Path

from jarvis.brain.tools.base import clip
from jarvis.config import settings

_SKILLS_DIR = Path(__file__).resolve().parents[4] / "skills"


def _skill_files() -> list[Path]:
    if not _SKILLS_DIR.is_dir():
        return []
    return sorted(_SKILLS_DIR.glob("*.md"))


def _title(p: Path) -> str:
    try:
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    except OSError:
        pass
    return p.stem


# Built-in INVOKABLE skills: pre-authored, composable multi-step sequences run through the same proven
# executor as macros (macros.run_steps). Unlike the Markdown playbooks (prose guidance read via
# read_skill), these DO things — each step is a tool call / spoken line / playbook load, run in order.
# Steps use module-registered tools (not agent built-ins like get_time, which aren't in the handler map).
_SKILL_MANIFESTS: dict[str, dict] = {
    "morning-briefing": {
        "desc": "Your day at a glance: open tasks, today's calendar, and unread email.",
        "steps": [
            {"tool": "notion_tasks", "args": {}},
            {"tool": "list_events", "args": {}},
            {"tool": "read_email", "args": {}},
        ],
    },
    "comms-check": {
        "desc": "Sweep your channels: unread Telegram and unread email in one pass.",
        "steps": [
            {"tool": "check_telegram", "args": {}},
            {"tool": "read_email", "args": {}},
        ],
    },
    "evening-review": {
        "desc": "Wind down: what's still open on your task list, then today's journal.",
        "steps": [
            {"tool": "notion_tasks", "args": {}},
            {"tool": "read_journal", "args": {}},
            {"say": "That's the day, sir. Rest well."},
        ],
    },
}


def _invokable_lines() -> list[str]:
    return [f"{name} — {m['desc']}" for name, m in _SKILL_MANIFESTS.items()]


async def list_skills(args: dict) -> str:
    if not settings.skills_enabled:
        return "My skills library is switched off right now, sir."
    files = _skill_files()
    invokable = _invokable_lines()
    parts: list[str] = []
    if invokable:
        parts.append(f"{len(invokable)} runnable skill(s) I can invoke: " + "; ".join(invokable))
    if files:
        items = [f"{p.stem} — {_title(p)}" for p in files]
        parts.append(f"{len(items)} reference playbook(s): " + "; ".join(items))
    if not parts:
        return "I don't have any skills yet, sir."
    return " ".join(parts)


async def invoke_skill(args: dict) -> str:
    """Run a built-in composable skill by name — its steps fire in order (tools/spoken/playbook)."""
    if not settings.skills_enabled:
        return "My skills library is switched off right now, sir."
    name = (args.get("name") or "").strip().lower().replace(" ", "-")
    if not name:
        return "Which skill should I run, sir?"
    m = _SKILL_MANIFESTS.get(name)
    if m is None:
        match = next((k for k in _SKILL_MANIFESTS if name in k), None)
        if match is None:
            avail = ", ".join(_SKILL_MANIFESTS)
            return f"I don't have a runnable skill called '{name}', sir. I can run: {avail}."
        name, m = match, _SKILL_MANIFESTS[match]
    from jarvis.brain.tools.macros import run_steps  # lazy (avoid import cycle)

    # authorized=True is safe here and ONLY here: invoke_skill is confirm-gated whenever the skill's
    # own steps contain a gated tool, so reaching this line already means the owner said yes.
    return await run_steps(m["steps"], f"Running skill '{name}' — {m['desc']}", authorized=True)


def skill_steps(name: str) -> list:
    """The steps a named skill would run — used by the confirm gate to decide whether invoking it
    needs the owner's yes. Tolerant of partial names, exactly like invoke_skill's own lookup."""
    key = (name or "").strip().lower().replace(" ", "-")
    m = _SKILL_MANIFESTS.get(key)
    if m is None:
        match = next((k for k in _SKILL_MANIFESTS if key and key in k), None)
        m = _SKILL_MANIFESTS.get(match) if match else None
    return list((m or {}).get("steps") or [])


async def read_skill(args: dict) -> str:
    if not settings.skills_enabled:
        return "My skills library is switched off right now, sir."
    name = (args.get("name") or "").strip().lower().removesuffix(".md")
    if not name:
        return "Which skill should I open, sir?"
    p = _SKILLS_DIR / f"{name}.md"
    if not p.is_file():
        # fuzzy: first skill whose stem contains the request
        match = next((f for f in _skill_files() if name in f.stem.lower()), None)
        if match is None:
            return f"I don't have a '{name}' skill, sir. Try list_skills."
        p = match
    try:
        return clip(p.read_text(encoding="utf-8", errors="ignore"), 6000)
    except OSError as e:
        # Same shape as tool_error so tool_failed() recognises it (see contacts.py for why).
        return f"I couldn't complete the skill read just now, sir ({type(e).__name__})."


SCHEMAS = [
    {"type": "function", "function": {
        "name": "list_skills",
        "description": "List your available skill playbooks (coding, self-improvement, your own "
                       "architecture, languages…). Use before a specialised task to see what guidance you have.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "read_skill",
        "description": "Open one of your reference playbooks by name (e.g. 'self-improvement', 'python', "
                       "'jarvis-architecture'). Read the relevant one before editing your own code.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Skill name (filename stem)."}},
            "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "invoke_skill",
        "description": "RUN a built-in composable skill — its steps (tool calls / spoken lines) fire in "
                       "order. Use for a named routine like 'morning-briefing' (tasks+calendar+email), "
                       "'comms-check' (telegram+email), or 'evening-review'. list_skills shows what's runnable.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Runnable skill name, e.g. 'morning-briefing'."}},
            "required": ["name"]}}},
]

HANDLERS = {"list_skills": list_skills, "read_skill": read_skill, "invoke_skill": invoke_skill}
