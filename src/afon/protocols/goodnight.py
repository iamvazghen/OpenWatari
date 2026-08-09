"""Protocol GOODNIGHT — stop Afon's edge. PC_LINK-routed; this file deliberately does nothing.

Until 2026-08-01 this script ran ``taskkill /PID <parent_pid> /F`` after a short pause. On the VPS
that parent pid is the BRAIN, not the edge — so "goodnight" would have killed the brain, which
``Restart=always`` revives seconds later: the owner says goodnight, Afon dies, and comes straight
back. The registry's ``pc_command`` does the right thing instead: it drops the
``edge_stopped_by_owner`` marker (so AfonEdgeGuard treats the silence as ORDERED rather than a
crash to undo) and stops the edge on the laptop, leaving pc_agent up so phoenix can revive him.

Kept (and inert, exiting non-zero) so the authorization gate still finds a script, and so a misrouted
call fails loudly instead of appearing to succeed.
"""

from __future__ import annotations

import sys

_MESSAGE = ("goodnight is PC_LINK-routed and must not be launched as a script — "
            "use run_protocol_async, which forwards it to the laptop")


def main() -> int:
    sys.stderr.write(_MESSAGE + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
