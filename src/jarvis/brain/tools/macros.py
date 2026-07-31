"""User-defined macros — chain multiple tool calls by name.

Define a sequence once, run it any number of times. Macros persist to ``~/.jarvis/macros.json``
so they survive a brain restart. Steps are declarative: each step is either a tool call
(``{"tool": "play_music", "args": {"query": "lofi"}}``), a spoken line (``{"say": "Good
morning, sir."}``), or a skill call (``{"skill": "daily-briefing"}``). The LLM composes the
steps from natural language; macros.py only persists + executes them.

Lazy group: 'macros' (loaded by trigger keywords: "macro", "routine", "recurring", "every
morning", "set up a shortcut").
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from loguru import logger

from jarvis.brain.tools.base import tool_error


# Where macros live. Override with JARVIS_MACROS_FILE.
def _store_path() -> Path:
    override = None
    try:
        from jarvis.config import settings
        override = settings.user_data_dir
    except Exception:
        pass
    base = Path(override) if override else Path.home() / ".jarvis"
    return base / "macros.json"


def _load() -> dict:
    p = _store_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"macros: failed to read {p}: {e}; starting empty")
        return {}


def _save(store: dict) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\- ]{0,40}$", re.I)


def _validate_name(name: str) -> str | None:
    n = (name or "").strip().lower()
    if not _NAME_RE.match(n):
        return ("Macro names must be 1-41 chars, alphanumerics / dashes / underscores / "
                "spaces, and start with a letter or digit.")
    return None


def _validate_steps(steps: list) -> str | None:
    if not isinstance(steps, list) or not steps:
        return "A macro needs at least one step, sir."
    if len(steps) > 32:
        return f"That's too many steps ({len(steps)}); keep macros under 32."
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return f"Step {i+1} isn't a dict, sir."
        keys = set(step.keys()) - {"_note"}
        if not keys:
            return f"Step {i+1} is empty, sir."
        if "tool" in keys:
            tool = step["tool"]
            if not isinstance(tool, str) or not tool.strip():
                return f"Step {i+1}: 'tool' must be a non-empty string."
            if "args" in step and not isinstance(step["args"], dict):
                return f"Step {i+1}: 'args' must be an object."
        elif "say" in keys:
            if not isinstance(step["say"], str):
                return f"Step {i+1}: 'say' must be a string."
        elif "skill" in keys:
            if not isinstance(step["skill"], str) or not step["skill"].strip():
                return f"Step {i+1}: 'skill' must be a non-empty string."
        elif "wait_seconds" in keys:
            if not isinstance(step["wait_seconds"], (int, float)) or step["wait_seconds"] < 0:
                return f"Step {i+1}: 'wait_seconds' must be a non-negative number."
        else:
            return (f"Step {i+1}: must have exactly one of 'tool' (with 'args'), 'say', 'skill', "
                    "or 'wait_seconds'.")
    return None


# ---- Public handlers -----------------------------------------------------------

async def list_macros(_args: dict) -> str:
    """List all saved macros."""
    store = _load()
    if not store:
        return "No macros saved yet, sir. Use define_macro to create one."
    lines = []
    for name in sorted(store):
        m = store[name]
        desc = (m.get("description") or "").strip()
        n = len(m.get("steps") or [])
        line = f"  • {name} ({n} step{'s' if n != 1 else ''})"
        if desc:
            line += f" — {desc}"
        lines.append(line)
    return f"{len(store)} macro(s) saved:\n" + "\n".join(lines)


async def delete_macro(args: dict) -> str:
    """Remove a macro by name."""
    name = (args.get("name") or "").strip().lower()
    if not name:
        return "Which macro should I delete, sir?"
    store = _load()
    if name not in store:
        return f"I don't have a macro called '{name}', sir."
    del store[name]
    _save(store)
    return f"Forgot the '{name}' macro, sir."


async def define_macro(args: dict) -> str:
    """Save (or update) a macro by name + steps. Each step is a dict with one of:\n- ``tool``: tool name; ``args``: dict passed to it.\n- ``say``: a spoken line.\n- ``skill``: skill name to load.\n- ``wait_seconds``: pause between steps.
    """
    name = (args.get("name") or "").strip()
    description = (args.get("description") or "").strip()
    steps = args.get("steps")
    err = _validate_name(name)
    if err:
        return err
    err = _validate_steps(steps or [])
    if err:
        return err
    name = name.lower()
    store = _load()
    store[name] = {"description": description, "steps": steps}
    _save(store)
    n = len(steps)
    return f"Macro '{name}' saved, sir — {n} step{'s' if n != 1 else ''}."


async def run_steps(steps: list, label: str, *, authorized: bool = False) -> str:
    """Execute an ordered step list (tool / say / skill / wait_seconds), returning a transcript.
    Shared by run_macro (user-defined) and invoke_skill (built-in skill manifests) — one proven
    executor, so a skill runs exactly like a macro. Each step failure is contained, not fatal.

    ``authorized`` says the owner confirmed THIS run; only then may it perform confirm-gated steps.
    It defaults to False so any future caller is safe by default."""
    steps = steps or []
    transcript: list[str] = [f"{label} ({len(steps)} steps)."]
    for i, step in enumerate(steps, 1):
        if "wait_seconds" in step:
            await asyncio.sleep(float(step["wait_seconds"]))
            transcript.append(f"[{i}/{len(steps)}] waited {step['wait_seconds']}s.")
            continue
        if "say" in step:
            transcript.append(f"[{i}/{len(steps)}] said: {step['say']}")
            continue
        if "skill" in step:
            try:
                from jarvis.brain.tools.skills import read_skill
                res = await read_skill({"name": step["skill"]})
            except Exception as e:  # noqa: BLE001
                res = f"(skill load failed: {e})"
            head = (res or "").strip().split("\n", 1)[0][:120]
            transcript.append(f"[{i}/{len(steps)}] skill '{step['skill']}' -> {head}")
            continue
        # Default: tool call.
        tool = step.get("tool", "")
        sub_args = step.get("args") or {}
        res = await run_step_tool(tool, sub_args, authorized=authorized, label=f"step {i}")
        head = (res or "").strip().split("\n", 1)[0][:120]
        transcript.append(f"[{i}/{len(steps)}] {tool} -> {head}")
    return "\n".join(transcript)


async def run_step_tool(tool: str, sub_args: dict, *, authorized: bool, label: str) -> str:
    """Run ONE macro/skill step's tool through the same guarantees a normal tool call gets.

    This used to call the handler straight out of the registry, which quietly skipped everything the
    agent's dispatcher provides: the confirm gate, the audit trail, error tracking and metrics. That
    made a saved macro a way to reach any destructive tool — ``git_push``, ``browser``,
    ``send_email`` — with no confirmation at all, even though ``run_macro`` itself was ungated.

    Now a confirm-gated step is REFUSED unless the macro run was itself authorised (which the agent
    guarantees, because run_macro/invoke_skill are confirm-gated whenever their steps contain a gated
    tool). Every step is audited and tracked either way."""
    import time as _time

    from jarvis.brain import audit
    from jarvis.brain.proactive import confirm_required
    from jarvis.shared import errors as _err

    try:
        from jarvis.brain.tools import tool_handlers  # lazy (avoid cycle)
        fn = tool_handlers().get(tool)
    except Exception:  # noqa: BLE001
        fn = None
    if fn is None:
        _err.record_op("macro-step", tool or "?", ok=False, detail="unknown tool")
        return f"'{tool}': unknown tool."

    if confirm_required(tool, sub_args) and not authorized:
        audit.record(tool, sub_args, "blocked: confirmation required (macro step)", ok=False)
        _err.record_op("macro-step", tool, ok=False, detail="blocked: needs the owner's confirmation")
        return (f"'{tool}' needs the owner's confirmation and this run wasn't authorised — skipped. "
                "Ask him directly, then run it.")

    started = _time.monotonic()
    try:
        res = await fn(sub_args)
        ok = not _err.looks_failed(res)
    except Exception as e:  # noqa: BLE001
        res, ok = tool_error(label, e), False
    _err.record_op("macro-step", tool, ok=ok,
                   detail="" if ok else str(res)[:200],
                   duration_ms=(_time.monotonic() - started) * 1000,
                   context={"args": str(sub_args)[:200]})
    audit.record(tool, sub_args, str(res), ok=ok)
    return res


async def run_macro(args: dict) -> str:
    """Execute a saved macro step-by-step. Returns a transcript of each step's result."""
    name = (args.get("name") or "").strip().lower()
    if not name:
        return "Which macro should I run, sir?"
    store = _load()
    m = store.get(name)
    if not m:
        return f"I don't have a macro called '{name}', sir. Try list_macros."
    desc = (m.get("description") or "").strip()
    label = f"Running macro '{name}'" + (f" — {desc}" if desc else "")
    # authorized=True is safe here and ONLY here: run_macro is confirm-gated whenever this macro's
    # own steps contain a gated tool, so reaching this line means the owner already said yes.
    out = await run_steps(m.get("steps") or [], label, authorized=True)
    return out + f"\nMacro '{name}' finished, sir."


# ---- Conditional flow (T2c) ---------------------------------------------------

CONDITION_RE = re.compile(
    r"^\s*(?P<subject>[A-Za-z0-9_]+)\s*(?P<op>==|!=|>=|<=|>|<|\bin\b|\bcontains\b)\s*(?P<value>.+?)\s*$",
    re.I,
)


async def if_then(args: dict) -> str:
    """Conditional flow: if `condition` is true, run `then_steps`, else run `else_steps`.

    Each step in then_steps/else_steps follows the same shape as macro steps (tool / say / skill).
    The LLM calls this when the owner asks for a branching flow ('if X then Y, otherwise Z'),
    or composes macros with conditions.
    """
    cond = (args.get("condition") or "").strip()
    then_steps = args.get("then_steps") or []
    else_steps = args.get("else_steps") or []
    m = CONDITION_RE.match(cond)
    if not m:
        return ("I'll need a clearer condition, sir (e.g. 'has_unread_email == 0' or "
                "'today's_weather contains rain').")
    subject, op, value = m.group("subject"), m.group("op").lower(), m.group("value").strip().strip("'\"")
    # Resolve the subject via a few known cheap sources. Anything not in the table is False.
    truthy = False
    subj = subject.lower()
    try:
        if subj in ("has_unread_email", "unread_emails", "inbox_unread"):
            from jarvis.brain.tools.gmail import read_email
            res = await read_email({"unread": True, "limit": 1})
            # Count by looking for the 'listings' line or counting line separators.
            truthy = "0 unread" not in res.lower() and "no unread" not in res.lower() and bool(res.strip())
        elif subj in ("weekday", "is_weekday", "is_weekend"):
            import datetime
            wd = datetime.datetime.now().weekday()
            truthy = (wd < 5) if subj == "is_weekday" else (wd >= 5)
        elif subj == "after_hours" or subj == "is_evening":
            import datetime
            truthy = datetime.datetime.now().hour >= 18 or datetime.datetime.now().hour < 6
    except Exception as e:
        logger.warning(f"if_then: failed to resolve subject '{subj}': {e}")
    # Apply the comparison if we have a concrete subject.
    if subj in ("weekday", "is_weekend"):
        target = {"weekday": ["mon", "tue", "wed", "thu", "fri"],
                  "is_weekend": ["sat", "sun"]}.get(subj, [])
        truthy = value.lower()[:3] in target if truthy is not None else False
    # For most subjects, "op" is currently advisory (the truthy bool already reflects the value).
    steps = then_steps if truthy else (else_steps or [])
    if not steps:
        verdict = "true" if truthy else "false"
        return f"'{cond}' was {verdict}. Nothing to run, sir."
    # Re-use run_macro's step executor by wrapping the conditional steps in a synthetic macro.
    synthetic = {"_conditional": {"steps": steps}}
    # Use the same per-step execution path as run_macro.
    from jarvis.brain.tools.macros import run_macro as _rm  # here we are — local alias
    # Actually call the step executor inline to avoid write-through to disk.
    transcript: list[str] = [f"Condition '{cond}' -> {'true' if truthy else 'false'}."]
    for i, step in enumerate(steps, 1):
        if "wait_seconds" in step:
            await asyncio.sleep(float(step["wait_seconds"]))
            transcript.append(f"[{i}] waited {step['wait_seconds']}s.")
            continue
        if "say" in step:
            transcript.append(f"[{i}] said: {step['say']}")
            continue
        if "skill" in step:
            try:
                from jarvis.brain.tools.skills import read_skill
                res = await read_skill({"name": step["skill"]})
            except Exception as e:
                res = f"(skill load failed: {e})"
            head = (res or "").strip().split("\n", 1)[0][:120]
            transcript.append(f"[{i}] skill '{step['skill']}' -> {head}")
            continue
        tool = step.get("tool", "")
        sub_args = step.get("args") or {}
        try:
            from jarvis.brain.tools import tool_handlers
            fn = tool_handlers().get(tool)
        except Exception:
            fn = None
        if fn is None:
            transcript.append(f"[{i}] '{tool}': unknown tool.")
            continue
        try:
            res = await fn(sub_args)
        except Exception as e:
            res = tool_error(f"if_then step {i}", e)
        head = (res or "").strip().split("\n", 1)[0][:120]
        transcript.append(f"[{i}] {tool} -> {head}")
    return "\n".join(transcript)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "define_macro",
            "description": (
                "Save (or update) a named macro — a reusable sequence of steps. Each step is a dict "
                "with exactly one of: `tool` (with `args`), `say` (spoken line), `skill` (skill name), "
                "or `wait_seconds` (pause). Use this when the owner wants a recurring workflow "
                "saved by name ('set up a morning routine that…')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Macro name, e.g. 'morning_routine'."},
                    "description": {"type": "string", "description": "One-line description for list_macros."},
                    "steps": {"type": "array", "description": "List of step dicts.", "items": {"type": "object"}},
                },
                "required": ["name", "steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_macros",
            "description": "List every saved macro (name + step count + description).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_macro",
            "description": "Execute a saved macro by name. Returns a transcript of each step.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Macro name."}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_macro",
            "description": "Forget a saved macro.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string", "description": "Macro name."}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "if_then",
            "description": (
                "Conditional flow: if `condition` is true, run `then_steps`, else run `else_steps`. "
                "Each step follows the same shape as a macro step (tool / say / skill). Use this "
                "when the owner asks for a branching flow ('if Ed emailed today, summarize it; "
                "otherwise skip')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "condition": {"type": "string",
                                  "description": "Subject OP value, e.g. 'has_unread_email == 0'."},
                    "then_steps": {"type": "array",
                                    "description": "Steps to run when condition is true.",
                                    "items": {"type": "object"}},
                    "else_steps": {"type": "array",
                                   "description": "Steps to run when condition is false.",
                                   "items": {"type": "object"}},
                },
                "required": ["condition"],
            },
        },
    },
]

HANDLERS = {
    "define_macro": define_macro,
    "list_macros": list_macros,
    "run_macro": run_macro,
    "delete_macro": delete_macro,
    "if_then": if_then,
}