"""Resilient edge→brain WebSocket client (P0 #3 — survive a brain restart).

The laptop edge, the phone, and the glasses all talk to the 24/7 brain over the same
``shared/protocol.py`` WebSocket. Over a week the brain *will* restart (deploy, crash, OOM); a
naive single-connection client would then go silently mute. This client wraps the connection in a
**supervised loop**: on any drop it reconnects with exponential backoff + jitter, re-sends ``Hello``
to re-register the session, and keeps a protocol-level ping going so a half-open socket is detected
and recycled instead of hanging. Connection state is surfaced via ``on_state`` so a TUI can show
"reconnecting…" rather than freezing.

It is transport-only: it doesn't do audio or TTS. Wire ``on_event`` to feed ``StreamEvent`` deltas
into whatever speaks them (the Pipecat TTS stage, the phone, …), and call ``send_utterance`` with a
finished transcript.

    client = BrainClient(session_id="laptop-1", on_event=speak, on_state=show_state)
    asyncio.create_task(client.run())          # supervised; returns when stop() is called
    await client.send_utterance("what time is it")
"""

from __future__ import annotations

import asyncio
import random
from collections import deque
from typing import Awaitable, Callable

from loguru import logger

from afon.config import settings
from afon.shared.protocol import Barge, Hello, StreamEvent, Utterance

#: How many un-shipped error entries to hold while the brain link is down. A whole outage's worth of
#: chatter is not worth the memory, and the disk journal is the complete record either way.
_SPOOL_MAX = 200

EventCb = Callable[[StreamEvent], Awaitable[None] | None]
StateCb = Callable[[str], None]


class BrainClient:
    """A self-healing WebSocket link to the brain. One instance == one session."""

    def __init__(
        self,
        session_id: str,
        *,
        device_id: str = "laptop",
        headphones_connected: bool = False,
        on_event: EventCb | None = None,
        on_state: StateCb | None = None,
        url: str | None = None,
        token: str | None = None,
        backoff_initial: float = 0.5,
        backoff_max: float = 30.0,
        heartbeat_s: float = 20.0,
    ) -> None:
        self.session_id = session_id
        self.device_id = device_id
        self.headphones_connected = headphones_connected
        self._on_event = on_event
        self._on_state = on_state
        self._url = url or settings.brain_ws_url
        self._token = token if token is not None else settings.api_auth_token
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._heartbeat_s = heartbeat_s
        self._ws = None                 # live connection or None
        self._stop = False
        self._state = "idle"
        # Error entries logged while the link was down, replayed on the next connect (see
        # ship_error). Bounded: a long outage must not grow this without limit, and the oldest
        # entries are the least useful — the ones that explain a failure are near the end. The
        # journal on disk keeps everything regardless, so dropping the tail here loses no record.
        self._spool: deque[dict] = deque(maxlen=_SPOOL_MAX)

    @property
    def connected(self) -> bool:
        return self._ws is not None

    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            logger.info(f"brain link: {state}")
            if self._on_state:
                try:
                    self._on_state(state)
                except Exception:  # noqa: BLE001 — a UI hiccup must not break the link
                    pass

    async def run(self) -> None:
        """Supervised connect loop. Reconnects until ``stop()`` is called."""
        from websockets.asyncio.client import connect

        headers = {"Authorization": f"Bearer {self._token}"} if self._token else None
        backoff = self._backoff_initial
        while not self._stop:
            try:
                self._set_state("connecting")
                async with connect(
                    self._url,
                    additional_headers=headers,
                    ping_interval=self._heartbeat_s,   # heartbeat every 20s: detect a half-open socket
                    # …but allow up to 75s for the pong: a brain turn (LLM + sync tool) can briefly block
                    # the event loop, and a 20s timeout dropped the link MID-REPLY. Matches the server.
                    ping_timeout=75.0,
                    max_size=4 * 1024 * 1024,
                ) as ws:
                    self._ws = ws
                    self._set_state("connected")
                    backoff = self._backoff_initial    # reset after a clean connect
                    await self._send(
                        Hello(
                            session_id=self.session_id,
                            device_id=self.device_id,
                            headphones_connected=self.headphones_connected,
                        )
                    )
                    await self._drain_spool()          # AFTER Hello: the brain wants the session first
                    await self._reader(ws)             # returns when the socket closes
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — any connect/read error -> reconnect
                logger.warning(f"brain link error ({type(e).__name__}): {e}")
            finally:
                self._ws = None
            if self._stop:
                break
            self._set_state("reconnecting")
            # backoff + jitter so many clients don't reconnect in lockstep after a brain restart
            delay = min(backoff, self._backoff_max) * (0.7 + 0.6 * random.random())
            await asyncio.sleep(delay)
            backoff = min(backoff * 2, self._backoff_max)
        self._set_state("stopped")

    async def _reader(self, ws) -> None:
        async for raw in ws:
            ev = self._parse(raw)
            if ev is None or self._on_event is None:
                continue
            try:
                res = self._on_event(ev)
                if asyncio.iscoroutine(res):
                    await res
            except Exception:  # noqa: BLE001 — a bad handler must not drop the link
                logger.exception("brain-event handler failed")

    @staticmethod
    def _parse(raw) -> StreamEvent | None:
        try:
            return StreamEvent.model_validate_json(raw)
        except Exception:  # noqa: BLE001 — ignore malformed frames
            return None

    async def _send(self, msg) -> bool:
        ws = self._ws
        if ws is None:
            return False
        try:
            await ws.send(msg.model_dump_json())
            return True
        except Exception as e:  # noqa: BLE001 — the supervised loop will reconnect
            logger.warning(f"brain send failed ({type(e).__name__}); will reconnect")
            return False

    async def send_utterance(self, text: str, ts_user_stop_ms: int = 0) -> bool:
        """Send a finished transcript. Returns False if the link is down (caller may retry/queue)."""
        from afon.shared import errors as _err

        # Mint the correlation id HERE — this is the moment a turn begins for the whole system, so
        # everything the edge logs from now on, and everything the brain logs for this turn, shares
        # one key. Tracing a bad turn across two machines is then a single lookup.
        turn_id = _err.new_turn()
        return await self._send(
            Utterance(
                session_id=self.session_id,
                text=text,
                ts_user_stop_ms=ts_user_stop_ms,
                device_id=self.device_id,
                turn_id=turn_id,
            )
        )

    def ship_error(self, entry: dict) -> None:
        """Hand one journal entry to the brain (fire-and-forget).

        Synchronous by design: it is called from the loguru sink, which must never block or await.
        The entry is already on local disk, so the whole point is that observability can fail
        without taking a turn with it.

        While the link is down the entry goes to a bounded in-memory SPOOL and is replayed on the
        next connect. That closes a blind spot that mattered precisely when it hurt: an error from
        brain_client itself describes the very socket it would have shipped over, so 13 of 45 edge
        entries never reached the VPS journal — and `diagnose` therefore could not see
        brain-connection failures, which is exactly what the owner asks about when the link is bad.
        In memory, not on disk, because this runs inside the logging sink and must not do I/O; the
        journal on disk is already the durable copy, so a crash loses nothing but the uplink."""
        ws = self._ws
        if ws is None:
            self._spool.append(entry)
            return
        try:
            import asyncio

            from afon.shared.protocol import ErrorReport

            msg = ErrorReport(session_id=self.session_id, entry=entry)
            asyncio.get_running_loop().create_task(self._send(msg))
        except Exception:  # noqa: BLE001 — never let error reporting raise into the logger
            # No running loop (or the send could not even be scheduled) — spool it rather than drop
            # it. This is the path taken when the sink fires from a non-async thread.
            try:
                self._spool.append(entry)
            except Exception:  # noqa: BLE001
                pass

    async def _drain_spool(self) -> None:
        """Replay entries logged while the link was down. Called once per successful connect.

        Each is marked ``replayed`` so the brain journal cannot be misread as "this happened now" —
        the entry keeps its ORIGINAL timestamp, and a reader comparing it to arrival order deserves
        to know why they disagree."""
        if not self._spool:
            return
        from afon.shared.protocol import ErrorReport

        pending, self._spool = list(self._spool), deque(maxlen=_SPOOL_MAX)
        sent = 0
        for entry in pending:
            # _send RETURNS False on failure rather than raising, so this has to be a value check —
            # wrapping it in try/except would look right and silently drop the remainder.
            ok = False
            try:
                ok = await self._send(ErrorReport(session_id=self.session_id,
                                                  entry={**entry, "replayed": True}))
            except Exception:  # noqa: BLE001 — belt and braces; never raise out of the drain
                ok = False
            if not ok:                      # link died again mid-drain — keep what's left
                self._spool.extend(pending[sent:])
                break
            sent += 1
        if sent:
            logger.info(f"brain link: replayed {sent} error entr{'y' if sent == 1 else 'ies'} "
                        "logged while disconnected")

    async def barge(self) -> bool:
        """Tell the brain to cancel the in-flight turn (user started talking over Afon)."""
        return await self._send(Barge(session_id=self.session_id))

    async def stop(self) -> None:
        """Stop reconnecting and close the link."""
        self._stop = True
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
