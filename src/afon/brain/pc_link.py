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
import time
import uuid

from loguru import logger

#: How long to wait for the laptop to answer a websocket ping before calling the link dead (04.F4).
#: One turn is seconds, so this is the budget that matters: the 90s result window is for a slow
#: COMMAND, and using it to discover an absent laptop meant a minute and a half of silence where
#: the honest answer was available immediately.
PING_TIMEOUT_S = 3.0

#: A registered socket that has said nothing for this long has not proven it is alive. Longer than
#: two of the websocket keepalive intervals (20s) so a quiet-but-healthy link is never called dead,
#: shorter than the 75s ping_timeout that eventually tears the socket down — the window in which a
#: dead laptop used to still read as connected.
STALE_AFTER_S = 45.0


class PcLink:
    def __init__(self) -> None:
        self._ws = None                              # the connected laptop executor socket
        self._host: str | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._seen: float = 0.0                      # monotonic time of the last frame from it

    @property
    def active(self) -> bool:
        return self._ws is not None

    @property
    def host(self) -> str | None:
        return self._host

    @property
    def silent_for(self) -> float:
        """Seconds since the laptop last said anything. 0.0 when nothing is registered."""
        return 0.0 if self._ws is None else max(0.0, time.monotonic() - self._seen)

    @property
    def stale(self) -> bool:
        """Registered, but has not been heard from recently enough to be called connected."""
        return self._ws is not None and self.silent_for > STALE_AFTER_S

    def touch(self) -> None:
        """Note that the laptop just spoke. Called for every inbound frame."""
        self._seen = time.monotonic()

    async def reachable(self, timeout: float = PING_TIMEOUT_S) -> bool:
        """True if the laptop answers a websocket ping now.

        This is the cheap, honest answer to "is the laptop there", and it needs no change to the
        executor: pongs are answered by the websockets library itself, so an agent that is running
        but busy still replies. A socket can stay open for over a minute after the machine on the
        other end has gone — a closed lid, a dropped tunnel — and during that minute every device
        command was accepted and then waited ninety seconds to fail.
        """
        if self._ws is None:
            return False
        try:
            pong = self._ws.ping()
            await asyncio.wait_for(pong, timeout)
        except Exception as e:  # noqa: BLE001 — any failure here means "not reachable", not a crash
            logger.debug(f"pc-control: ping unanswered ({type(e).__name__})")
            return False
        self.touch()
        return True

    def register(self, ws, host: str | None = None) -> None:
        self._ws = ws
        self.touch()
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
        self.touch()
        fut = self._pending.pop(cmd_id, None)
        if fut and not fut.done():
            fut.set_result((ok, output))

    async def forward(self, op: str, args: dict, timeout: float = 90.0) -> str:
        """Send one PC op to the laptop executor and return its spoken result string."""
        if self._ws is None:
            raise ConnectionError("laptop not connected")
        # 04.F4 — prove the laptop is there BEFORE committing to the result window. Without this a
        # command sent into a dead socket is indistinguishable from a slow one for ninety seconds,
        # and the owner is told nothing at all in the turn where he asked.
        if not await self.reachable():
            raise ConnectionError(
                f"laptop did not answer a ping (silent {int(self.silent_for)}s)")
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
