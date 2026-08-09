"""Protocol RAGNAROK — restart the LAPTOP. PC_LINK-routed; this file deliberately does nothing.

Until 2026-08-01 this script shelled out to Windows `shutdown` with a POSIX fallback of
`shutdown -r +1`. That was written when the brain ran on the laptop. With the brain on the VPS the
fallback aims at the *server*; it fails today only because the service user has no sudo, which is a
permission accident rather than a design. The real command now lives in the protocol registry as
``pc_command`` and travels to the machine it belongs to (see ``brain/protocols.py``).

The file remains because ``run_protocol`` requires a protocol's script to be present before the
authorization gate passes, and the recovery DRILL exercises that gate — so it must exist and be
inert. It exits NON-ZERO rather than 0: if some future caller ever launches it, that should surface
as a failure, not as a success that quietly did nothing.
"""

from __future__ import annotations

import sys

_MESSAGE = ("ragnarok is PC_LINK-routed and must not be launched as a script — "
            "use run_protocol_async, which forwards it to the laptop")


def main() -> int:
    sys.stderr.write(_MESSAGE + "\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
