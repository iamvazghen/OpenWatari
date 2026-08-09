"""PC-control link — lets the 24/7 VPS brain execute on the owner's laptop.

The brain's system tools (files, processes, PowerShell, opening apps/URLs, the browser) run on
whatever host the brain runs on. On the VPS that's the wrong machine. So the laptop runs a small
executor (``edge/pc_agent.py``) that connects OUT to the brain over the tailnet and registers here;
the system tools then FORWARD each command to it and await the result. One executor (the laptop) at a
time. If none is connected, the tools fall back to running locally (which is correct when the brain
itself runs on the laptop), or report the laptop offline (on the VPS).

Security: the executor authenticates with the same bearer token as the voice socket, and the channel
rides the private Tailscale network. The existing system-tool guards (protected paths, Afon's own
secrets) still apply — they run inside the forwarded handler on the laptop.
"""

from __future__ import annotations

import asyncio
import json
import uuid

from loguru import logger


class PcLink:
    def __init__(self) -> None:
        self._ws = None                              # the connected laptop executor socket
        self._host: str | None = None
        self._pending: dict[str, asyncio.Future] = {}

    @property
    def active(self) -> bool:
        return self._ws is not None

    @property
    def host(self) -> str | None:
        return self._host

    def register(self, ws, host: str | None = None) -> None:
        self._ws = ws
        if host:
            self._host = host

    def unregister(self, ws) -> None:
        if self._ws is ws:
            self._ws = None
            self._host = None
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("laptop executor disconnected"))
            self._pending.clear()

    def resolve(self, cmd_id: str, ok: bool, output: str) -> None:
        fut = self._pending.pop(cmd_id, None)
        if fut and not fut.done():
            fut.set_result((ok, output))

    async def forward(self, op: str, args: dict, timeout: float = 90.0) -> str:
        """Send one PC op to the laptop executor and return its spoken result string."""
        if self._ws is None:
            raise ConnectionError("laptop not connected")
        cmd_id = uuid.uuid4().hex
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[cmd_id] = fut
        # Carry the turn id across to the laptop so a failure there is filed under the same
        # correlation key as the turn that triggered it.
        from afon.shared import errors as _err

        await self._ws.send(json.dumps({"type": "pc_command", "id": cmd_id, "op": op, "args": args,
                                        "turn_id": _err.current_turn()}))
        try:
            _ok, output = await asyncio.wait_for(fut, timeout)
            return output
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            logger.warning(f"pc-control: '{op}' timed out after {timeout}s")
            raise


# Process-wide link (one brain process, one laptop).
PC_LINK = PcLink()
