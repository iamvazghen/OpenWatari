"""Protocol PHOENIX — restart Afon's edge. PC_LINK-routed; this file deliberately does nothing.

Until 2026-08-01 this script ran ``taskkill /PID <parent_pid> /F`` and then started a fresh edge from
the repo root. Both halves broke when the brain moved to the VPS: ``taskkill`` doesn't exist on Linux,
the parent pid is now the BRAIN rather than the edge, and the edge it tried to spawn would have come
up on a server with no microphone. The working command is ``Start-ScheduledTask -TaskName
AfonEdgeRefresh`` in the protocol registry, which runs on the laptop where the audio devices are.

Kept (and inert, exiting non-zero) for the same reason as ``ragnarok.py``: the authorization gate
requires the script to be present, and a silent success would hide a misrouted call.
"""

from __future__ import annotations

import sys

_MESSAGE = ("phoenix is PC_LINK-routed and must not be launched as a script — "
            "use run_protocol_async, which forwards it to the laptop")


def main() -> int:
    sys.stderr.write(_MESSAGE + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
