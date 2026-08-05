"""Protocol tool — let Jarvis run a named, password-gated routine.

Jarvis must ASK for the password first (persona/memory rule) and pass it here; the runner
verifies it before anything executes. Wrong/missing password → nothing runs.
"""

from __future__ import annotations

from jarvis.brain.protocols import describe_protocols, run_protocol_async as _run
from jarvis.brain.tools.base import tool_error


async def run_protocol(args: dict) -> str:
    name = (args.get("name") or "").strip()
    password = (args.get("password") or "").strip()
    drill = bool(args.get("drill"))
    if not name:
        try:
            return f"Which protocol, sir? I have: {describe_protocols()}."
        except Exception as e:  # noqa: BLE001 — the registry reads settings; a bad one shouldn't crash
            return tool_error("protocol list", e)
    if not password:
        verb = "drill" if drill else "run"
        return f"Protocol {name} requires the password to {verb} it, sir. What is it?"
    # The runner reaches a laptop over PC_LINK and spawns processes. It handles its own expected
    # failures, but an unexpected one must not reach the owner as a bare exception name — and this
    # is the tool that stops and restarts machines, so its failure mode deserves to be legible.
    try:
        result = await _run(name, password, drill=drill)
    except Exception as e:  # noqa: BLE001
        return tool_error(f"{name} protocol", e)
    return result.spoken if (result.ok and result.spoken) else result.message


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "run_protocol",
            "description": (
                "Run a named protocol — a privileged routine that requires the owner's password. "
                "Available: 'goodnight' (stop Jarvis), 'phoenix' (restart Jarvis), 'ragnarok' "
                "(restart the laptop), 'backup' (archive memory), 'ping' (phone push test), "
                "'diagnostics' (write health report), 'auditpack' (archive audit logs), and "
                "'checkpoint' (archive non-secret context). NEVER call this without the password: "
                "if he names a "
                "protocol but hasn't given the password, ask him for it first, then call this "
                "with both. The runner rejects a wrong password. Set drill=true to REHEARSE a recovery "
                "protocol (verify it's ready) WITHOUT actually stopping/restarting anything."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Protocol name."},
                    "password": {"type": "string", "description": "The password the owner provided."},
                    "drill": {"type": "boolean", "description": "If true, rehearse (verify readiness) "
                              "without executing. Use when he says 'drill'/'test'/'rehearse' a protocol."},
                },
                "required": ["name", "password"],
            },
        },
    },
]

HANDLERS = {"run_protocol": run_protocol}
