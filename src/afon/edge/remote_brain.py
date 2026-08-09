"""RemoteBrain — the unified-brain twin of the in-process AfonBrain.

When ``AFON_BRAIN_MODE=remote`` (or ``auto`` and the brain is reachable), the laptop edge stops
running its own agent and becomes a thin client to the 24/7 VPS brain: there is then ONE Afon and
ONE memory across the laptop, phone, and glasses. Audio (STT/TTS, wake word, VAD) still runs locally
for privacy + mic latency; only the finished transcript goes to the brain, which streams the reply
back sentence-by-sentence for incremental TTS — same low-latency feel as local.

Pipeline placement is identical to AfonBrain: STT -> RemoteBrain -> TTS.
"""

from __future__ import annotations

import asyncio

from loguru import logger
from pipecat.frames.frames import Frame, InterruptionFrame, TranscriptionFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from afon.edge.brain_client import BrainClient
from afon.shared.protocol import StreamEvent, StreamKind


class RemoteBrain(FrameProcessor):
    """Thin client to the VPS brain, with a WARM LOCAL STANDBY (Phase 0.2).

    The VPS brain is a single point of failure: over a week it *will* be briefly unreachable (deploy,
    OOM, network). ``BrainClient`` already reconnects, but during the gap a naive client just drops the
    utterance and Afon goes mute. Instead, when a send can't reach the VPS we answer the turn with a
    LOCAL in-process ``AfonAgent`` — its own independent LLM chain — and speak that. Routing is
    decided per turn on the live link state, so the moment the VPS is back the next turn returns to it
    automatically (promote → serve-local → demote, with no explicit state machine). The local agent is
    built lazily, so the common case (VPS up) never pays for it.
    """

    def __init__(
        self,
        session_id: str = "laptop-edge",
        device_id: str = "laptop",
        headphones_connected: bool = False,
    ) -> None:
        super().__init__()
        self._client = BrainClient(
            session_id=session_id,
            device_id=device_id,
            headphones_connected=headphones_connected,
            on_event=self._on_event,
        )
        self._task: asyncio.Task | None = None
        self._fallback_agent = None          # lazily-built local AfonAgent (warm standby)
        self._local_task: asyncio.Task | None = None
        # Send this laptop's errors up to the brain, so the VPS journal is the union of all three
        # processes and "what did he actually experience?" is answerable from one place.
        try:
            from afon.shared import errors as _err

            _err.set_shipper(self._client.ship_error)
        except Exception:  # noqa: BLE001 — shipping is a bonus; the local journal is the source
            pass

    async def start(self, connect_timeout_s: float = 3.0) -> bool:
        """Begin the supervised link. Returns True once connected within the timeout (so the
        caller can fall back to the local brain if the VPS is unreachable)."""
        self._task = asyncio.create_task(self._client.run())
        deadline = asyncio.get_running_loop().time() + connect_timeout_s
        while asyncio.get_running_loop().time() < deadline:
            if self._client.connected:
                logger.info("RemoteBrain: linked to the VPS brain")
                return True
            await asyncio.sleep(0.1)
        logger.warning("RemoteBrain: VPS brain not reachable within timeout")
        return self._client.connected

    async def _on_event(self, ev: StreamEvent) -> None:
        # Assistant text and spoken tool-progress both go to TTS; lifecycle events are control-only.
        if ev.kind in (StreamKind.assistant, StreamKind.tool) and ev.delta.strip():
            await self.push_frame(TTSSpeakFrame(ev.delta.strip()))

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            await self._client.barge()          # tell the brain to cancel the in-flight turn
            if self._local_task and not self._local_task.done():
                self._local_task.cancel()       # also cancel any in-flight LOCAL standby turn
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TranscriptionFrame):
            text = (getattr(frame, "text", "") or "").strip()
            if text:
                await self._route_utterance(text)
            return  # consume; the remote reply arrives async via _on_event

        await self.push_frame(frame, direction)

    async def _route_utterance(self, text: str) -> str:
        """Send to the VPS brain if the link is live; otherwise fall back to the local standby.

        Returns "remote" or "local" (for logging/testing). The remote reply streams back async via
        ``_on_event``; the local reply is streamed inline here.
        """
        if self._client.connected and await self._client.send_utterance(text):
            logger.info(f"heard: {text!r} -> VPS brain")
            return "remote"
        logger.warning(f"VPS brain unreachable — answering {text!r} on the LOCAL standby brain")
        await self._answer_local(text)
        return "local"

    async def _ensure_fallback(self):
        """Build (once) + warm the local standby agent. A preset agent (tests) is left untouched."""
        if self._fallback_agent is None:
            from afon.brain.agent import AfonAgent

            agent = AfonAgent()
            warm = getattr(agent, "warmup", None)
            if callable(warm):
                try:
                    await warm()
                except Exception:  # noqa: BLE001 — warmup is best-effort
                    pass
            self._fallback_agent = agent
        return self._fallback_agent

    async def _answer_local(self, text: str) -> None:
        """Run one turn on the local standby agent and speak it (streamed sentence-by-sentence)."""
        async def _turn() -> None:
            try:
                agent = await self._ensure_fallback()
                spoke = False
                async for sentence in agent.respond_stream(text):
                    if sentence:
                        spoke = True
                        await self.push_frame(TTSSpeakFrame(sentence))
                if not spoke:
                    await self.push_frame(TTSSpeakFrame(
                        "The main brain's unreachable, sir, and I couldn't answer locally either."))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — a standby failure must not crash the edge
                logger.exception("local standby turn failed")
                await self.push_frame(TTSSpeakFrame(
                    "The main brain's unreachable, sir — I'm on the local standby and hit an error."))

        self._local_task = asyncio.create_task(_turn())
        await self._local_task

    async def stop(self) -> None:
        if self._local_task and not self._local_task.done():
            self._local_task.cancel()
        await self._client.stop()
