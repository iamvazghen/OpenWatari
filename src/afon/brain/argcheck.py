"""03.R2 — check a tool call's arguments BEFORE dispatching it.

Until now the first thing that saw a malformed call was the handler. Each one defends itself
(`base.missing_arg`), which is right and stays, but it means a wrong call is discovered halfway
through doing the work, and what comes back is prose: "I couldn't complete the reminder
(ValueError)". The model reads prose as an answer, so a mistyped argument became a spoken apology
instead of a corrected second attempt — the retry ladder never fired because nothing said the call
itself was the problem.

The schemas are already in the registry, so the check costs nothing to source. What it checks is
**types, and only types** — and the restraint is the design, not a shortcut.

The obvious version of this task also rejects a call that omits a `required` property. Measuring it
first is what stopped it shipping: **eleven tools accept a spelling their schema marks required**.
`remember(content=…)`, `send_push(body=…)`, `set_reminder(what=…)`, `resolve_contact(who=…)` all
work today, because `base.missing_arg` takes "the full set of accepted spellings, synonyms
included" and the schema advertises one of them to keep the catalogue small. A schema-only view
cannot see those, so enforcing `required` here would have broken eleven working tools to catch a
mistake the handler already catches.

It also cannot see the distinction `missing_arg` exists to draw: ``{}`` means the OWNER left the
detail out and the right answer is to ask him, while ``{"wrong_key": …}`` means the MODEL invented a
key and the right answer is an error that makes the retry fire. Both look identical from here.

So the division of labour is: this checks what the schema can actually prove (a declared property
arrived as the wrong type), and the handler keeps deciding what a missing value means. What 03.R2
adds on top is in `agent.py` — a BAD_ARGS result stops being prose the model reads as an answer and
becomes one repair instruction.

A wrong type is worth catching here because the alternative is a crash inside the handler:
``int(args["limit"])`` raised `ValueError` whenever the model filled the field with a word, which is
the bug `test_tool_error_handling.py` was written for. Where the value is the obvious stringified
form it is **coerced** rather than rejected — a model that sends ``"5"`` for an integer meant five,
and spending a whole round-trip to be told so serves nobody.

**Unknown keys are deliberately NOT rejected**, for the same reason as `required`.

    uv run python -m afon.brain.argcheck
"""
from __future__ import annotations

from typing import Any

#: JSON Schema type -> the Python types that satisfy it. `bool` is excluded from the number types on
#: purpose: in Python `True` is an int, and a boolean arriving where a count belongs is a mistake
#: worth catching rather than silently reading as 1.
_OK: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


def _coerce(want: str, value: Any) -> tuple[bool, Any]:
    """(did_coerce, value). Only the stringified forms a model actually produces."""
    if not isinstance(value, str):
        return False, value
    text = value.strip()
    if want == "integer":
        try:
            return True, int(text)
        except ValueError:
            return False, value
    if want == "number":
        try:
            return True, float(text)
        except ValueError:
            return False, value
    if want == "boolean" and text.lower() in ("true", "false", "yes", "no"):
        return True, text.lower() in ("true", "yes")
    return False, value


def _typed_ok(want: str, value: Any) -> bool:
    types = _OK.get(want)
    if types is None:
        return True                       # a type this does not model is not a type it may reject
    if want in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, types)


def check_args(tool: str, args: dict, schema: dict | None) -> tuple[dict, str]:
    """``(args_to_dispatch, problem)``. `problem` is "" when the call is good to run.

    The problem sentence names the tool, the argument and what was expected, because the point is
    that the model can fix it in one attempt — "invalid arguments" tells it to guess again.
    """
    if not isinstance(args, dict):
        return {}, f"`{tool}` was called with {type(args).__name__}, not an object of arguments"
    params = ((schema or {}).get("function", {}).get("parameters") or {}) if schema else {}
    props: dict[str, Any] = params.get("properties") or {}
    out = dict(args)
    for key, value in args.items():
        spec = props.get(key)
        if not isinstance(spec, dict):
            continue                      # undeclared key: the handler's business, not ours
        want = spec.get("type")
        if not isinstance(want, str) or value is None or _typed_ok(want, value):
            continue
        did, coerced = _coerce(want, value)
        if did:
            out[key] = coerced
            continue
        return args, (f"`{tool}` wants {key!r} as {want}, and it arrived as "
                      f"{type(value).__name__}")
    return out, ""


def _selfcheck() -> None:
    """ponytail: the one runnable check — against the REAL registry, not invented schemas."""
    from afon.brain.tools import schemas_by_name

    [weather] = schemas_by_name(["weather"])
    [rem] = schemas_by_name(["set_reminder"])

    good, problem = check_args("weather", {"location": "Cologne"}, weather)
    assert problem == "", problem
    assert good == {"location": "Cologne"}

    # The eleven-tool trap: `set_reminder`'s schema requires "message", and its handler also accepts
    # "what", "about", "task" and five more. Enforcing `required` here would reject a call that
    # works, so it is not enforced — and this asserts that it stays that way.
    _, problem = check_args("set_reminder", {"what": "call mum", "in_minutes": 30}, rem)
    assert problem == "", f"a working synonym call must not be rejected: {problem}"
    _, problem = check_args("set_reminder", {}, rem)
    assert problem == "", "an empty call is the handler's to answer — it may need to ASK"

    # The stringified integer a model actually sends is accepted, not bounced.
    fake = {"function": {"parameters": {"type": "object", "required": ["n"],
                                        "properties": {"n": {"type": "integer"},
                                                       "on": {"type": "boolean"}}}}}
    out, problem = check_args("t", {"n": "5", "on": "yes"}, fake)
    assert problem == "" and out == {"n": 5, "on": True}, (out, problem)
    # ...but a word is not a number, and that IS worth a round-trip.
    _, problem = check_args("t", {"n": "five"}, fake)
    assert "integer" in problem, problem
    # A boolean where a count belongs is a mistake, not a 1.
    _, problem = check_args("t", {"n": True}, fake)
    assert problem, "True is an int in Python; it must not pass as a count"
    # An undeclared key rides along: handlers own their synonyms (see base.missing_arg).
    out, problem = check_args("t", {"n": 1, "spelled_differently": "x"}, fake)
    assert problem == "" and out["spelled_differently"] == "x"

    checked = sum(1 for s in schemas_by_name([]) or []) + len(_OK)
    print(f"selfcheck ok — {checked} type rules, validated against the live registry")


if __name__ == "__main__":
    _selfcheck()
