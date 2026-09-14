"""01.R4 — what Afon can and cannot do, read off the real registry and the real config.

The question "what can you do?" used to be answered from prose. `personality/operating-rules.md`
names sixteen tools by hand, and every one of them is a promise that rots the moment a tool is
renamed, removed, or left unconfigured. The failure is not cosmetic: a self-model assembled from
prose lets Afon claim a capability he does not have, and the owner finds out by asking for it.

Two halves, and the second is the one that was missing:

* **Can.** The model already holds the tool catalogue every turn, so the "can" half needs no
  prompt text at all — it needs the prose to stop naming tools that are not there, which
  `test_registry_complete.py` now enforces.
* **Cannot.** Nothing told him which integrations are dark. `notion`, `smarthome`, `phone`,
  `maps` and the rest each know whether they are configured and what they need, and none of that
  reached the prompt — so "text her" against an unconfigured Twilio produced a tool call, a
  not-configured string, and an apology, instead of one honest sentence up front.

Generated, so it cannot drift: a new integration module appears here the day it is added, and a
retired one disappears the day it goes. Nothing in this file lists a capability by name.

    uv run python -m afon.brain.selfmodel
"""
from __future__ import annotations

from dataclasses import dataclass

from afon.brain.tools import _MODULES


@dataclass(frozen=True)
class Integration:
    """One capability that depends on something outside this program."""
    name: str
    tools: tuple[str, ...]
    ready: bool
    needs: str

    def said(self) -> str:
        return f"{self.name} (needs {self.needs})"


def _probe(mod):
    """How a tool module reports whether its dependency is actually there.

    Modules declare either their own `_configured()` or borrow one (the Google modules import
    `configured` from `afon.brain.google`). A module with `_NEEDS` and no probe at all is a bug —
    it can tell the owner what it wants but never whether it has it — so it is reported as not
    ready rather than assumed fine, because the failure of a silent default here is Afon claiming
    a capability he does not have.
    """
    return getattr(mod, "_configured", None) or getattr(mod, "configured", None)


def integrations() -> list[Integration]:
    """Every tool module that declares an outside dependency, with its live readiness."""
    out: list[Integration] = []
    for mod in _MODULES:
        needs = (getattr(mod, "_NEEDS", "") or "").strip()
        if not needs:
            continue
        probe = _probe(mod)
        try:
            ready = bool(probe()) if probe else False
        except Exception:                                        # noqa: BLE001
            ready = False        # a probe that throws is not a configured integration
        names = tuple(s["function"]["name"] for s in getattr(mod, "SCHEMAS", []))
        out.append(Integration(mod.__name__.rsplit(".", 1)[-1], names, ready, needs))
    return sorted(out, key=lambda i: i.name)


def unavailable() -> list[Integration]:
    """The half that was missing from the prompt: what he genuinely cannot do right now."""
    return [i for i in integrations() if not i.ready]


def prompt_block() -> str:
    """The generated line for the system prompt, or nothing at all.

    Only the DARK integrations are named, and only their NAMES. Listing the working ones would
    restate the catalogue the model is already holding — the per-turn cost 03.R5 exists to defend —
    and the useful fact is always the negative one. The `needs` text stays out: it is written for
    the owner, it is what `spoken()` is for, and a per-turn cost that grows every time someone
    writes a more helpful `_NEEDS` string is a cost nobody is watching. Empty when everything is
    configured, which is the point: a block present on every turn stops being read.
    """
    dark = unavailable()
    if not dark:
        return ""
    return ("# Not set up on this install, so you genuinely cannot do these — say so plainly and "
            "offer to walk him through it; do not call their tools and then apologise: "
            + ", ".join(i.name for i in dark))


def spoken() -> str:
    """The answer to 'what can't you do?', for a person rather than a prompt."""
    dark = unavailable()
    if not dark:
        return "Everything I'm wired for is configured, sir."
    if len(dark) == 1:
        return f"One thing isn't set up, sir: {dark[0].said()}."
    listed = "; ".join(i.said() for i in dark)
    return f"{len(dark)} things aren't set up, sir: {listed}."


def named_tools(text: str) -> set[str]:
    """Tool names a piece of prose claims exist — anything in `backticks` that looks like one."""
    import re

    return {m for m in re.findall(r"`([a-z][a-z0-9_]{2,})`", text or "") if "_" in m or m.islower()}


def _selfcheck() -> None:
    """ponytail: the one runnable check — generated from the registry, and honest about gaps."""
    ints = integrations()
    assert ints, "no integration declares _NEEDS — introspection has stopped working"
    assert all(i.needs for i in ints)
    assert all(isinstance(i.ready, bool) for i in ints)
    # Every name reported is a real module, and every tool named is a real tool.
    from afon.brain.tools import tool_handlers

    known = set(tool_handlers())
    for i in ints:
        assert set(i.tools) <= known, f"{i.name} advertises tools nothing handles: {set(i.tools) - known}"
    block = prompt_block()
    assert all(i.name in block for i in unavailable()), block
    assert all(i.name not in block for i in ints if i.ready), block
    said = spoken()
    assert said and said.endswith("."), said
    print(f"selfcheck ok — {len(ints)} integrations, {len(unavailable())} dark")
    print(spoken())


if __name__ == "__main__":
    _selfcheck()
