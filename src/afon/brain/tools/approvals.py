"""Approval-queue tools (Phase 4.2) — the human in the loop for autonomous work.

When Afon works unattended he refuses outward/destructive steps and QUEUES them. These tools let the
owner clear that queue by voice:
  * ``list_approvals``  — "what needs my approval?"
  * ``approve_action``  — "approve the first one" / "approve the email" -> executes it NOW
  * ``reject_action``   — "no, drop that one"

Approving runs the exact tool + args the worker wanted, with the owner's consent supplied at that moment.
"""

from __future__ import annotations

from afon.brain.approvals import APPROVALS
from afon.brain.tools.base import tool_error


async def list_approvals(args: dict) -> str:
    try:
        body = APPROVALS.render()
    except Exception as e:  # noqa: BLE001
        return tool_error("list approvals", e)
    if not body:
        return "Nothing's waiting on your approval, sir."
    n = len(APPROVALS.pending())
    head = "One action is waiting on you, sir:" if n == 1 else f"{n} actions are waiting on you, sir:"
    return f"{head}\n{body}"


async def approve_action(args: dict) -> str:
    topic = (args.get("topic") or args.get("id") or "").strip()
    try:
        ap = APPROVALS.find(topic)
        if ap is None:
            if not APPROVALS.pending():
                return "There's nothing pending to approve, sir."
            return (f"I couldn't tell which one you meant by '{topic}', sir. "
                    f"Here's what's waiting:\n{APPROVALS.render()}")
        ok, out = await APPROVALS.approve(ap.id)
    except Exception as e:  # noqa: BLE001
        return tool_error("approve action", e)
    if not ok:
        return out if out.endswith(".") else f"{out}."
    return f"Done, sir — {ap.summary}. {out}".strip()


async def reject_action(args: dict) -> str:
    topic = (args.get("topic") or args.get("id") or "").strip()
    try:
        ap = APPROVALS.find(topic)
        if ap is None:
            if not APPROVALS.pending():
                return "There's nothing pending, sir."
            return (f"I couldn't tell which one you meant by '{topic}', sir. "
                    f"Here's what's waiting:\n{APPROVALS.render()}")
        APPROVALS.reject(ap.id, (args.get("reason") or "").strip())
    except Exception as e:  # noqa: BLE001
        return tool_error("reject action", e)
    return f"Dropped it, sir — I won't run {ap.summary}."


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_approvals",
            "description": (
                "List the outward actions Afon deferred during autonomous work and queued for the "
                "owner's approval. Use for 'what needs my approval', 'what are you waiting on me for', "
                "'anything pending'."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_action",
            "description": (
                "APPROVE a queued action and execute it now. Use for 'approve that', 'approve the first "
                "one', 'go ahead and send it', 'yes, do it'. Identify it by ordinal ('first'), its id, or "
                "a word from it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string",
                              "description": "Which one: 'first', an id, or a word from the action."},
                },
                # This tool EXECUTES a queued action; approving the wrong one is not recoverable.
                # Without a topic it can only list what's pending, so demand the identifier.
                "required": ["topic"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reject_action",
            "description": ("Reject a queued action so it never runs. Use for 'no, drop that', "
                            "'don't send it', 'reject the first one'. approve_action runs it "
                            "instead; list_approvals shows what's waiting."),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "description": "Which one: 'first', an id, or a word."},
                    "reason": {"type": "string", "description": "Optional reason."},
                },
                "required": ["topic"],   # reason stays optional; the identifier does not
            },
        },
    },
]

HANDLERS = {
    "list_approvals": list_approvals,
    "approve_action": approve_action,
    "reject_action": reject_action,
}
