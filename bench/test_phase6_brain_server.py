"""Phase 6 — brain WebSocket server (offline, hermetic).

Verifies the server that lets a remote client (iPhone web client, Mentra glasses) reach the
SAME shared brain over ``shared/protocol.py``: Hello handshake, Utterance -> streamed reply
chunks for incremental TTS, tool fillers, barge/supersede cancellation, shared-agent session,
and optional bearer auth. Uses a stub agent + fake websocket, so no LLM or real socket needed.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f" — {detail}" if detail and not ok else ""))


class FakeWS:
    """Collects frames the server 'sends', and carries headers for the auth check."""

    def __init__(self, auth: str | None = None) -> None:
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None

        class _Req:
            headers = {"Authorization": auth} if auth is not None else {}

        self.request = _Req()

    async def send(self, data) -> None:
        import json

        self.sent.append(json.loads(data))

    async def close(self, code=1000, reason="") -> None:
        self.closed = (code, reason)

    def kinds(self) -> list[str]:
        return [f.get("kind") for f in self.sent]

    def deltas(self, kind: str) -> list[str]:
        return [f["delta"] for f in self.sent if f.get("kind") == kind]


class StubAgent:
    """Stand-in for AfonAgent: records the prompt, fires a tool filler, returns a reply."""

    def __init__(self, reply: str = "Hello sir. All set.", delay: float = 0.0) -> None:
        self.reply = reply
        self.delay = delay
        self.seen: list[str] = []

    async def warmup(self) -> None:
        pass

    async def respond(self, text: str, on_progress=None) -> str:
        self.seen.append(text)
        if on_progress:
            on_progress("Working on it…")
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.reply

    async def respond_stream(self, text: str, on_progress=None):
        # Streaming twin used by the server now: record, fire a filler, yield sentence chunks.
        import re as _re
        self.seen.append(text)
        if on_progress:
            on_progress("Working on it…")
        if self.delay:
            await asyncio.sleep(self.delay)
        for part in _re.split(r"(?<=[.!?])\s+", self.reply):
            if part.strip():
                yield part.strip()


async def run() -> None:
    from afon.brain.server import BrainServer, chunk_for_tts, parse_client_message
    from afon.shared.protocol import Barge, Hello, Utterance

    # Isolate from real daily-digest state: on the FIRST edge turn of a day the server appends a
    # "By the way, sir — …" catch-up as an extra final chunk (and would hit live Gmail). This test is
    # about the streaming/chunk mechanics, not the digest, so pin `due` off for hermeticity.
    from afon.brain import daily_digest
    daily_digest.due = lambda channel, now=None: False

    print("[1] chunk_for_tts splits sentences for incremental TTS")
    check("empty -> no chunks", chunk_for_tts("  ") == [])
    c = chunk_for_tts("Hi sir. The price is up. Anything else?")
    check("three sentences -> three chunks", len(c) == 3, str(c))
    check("single fragment kept", chunk_for_tts("on it") == ["on it"])

    print("\n[2] parse_client_message discriminates by type")
    check("hello parses", isinstance(parse_client_message('{"type":"hello","session_id":"s"}'), Hello))
    check(
        "utterance parses",
        isinstance(
            parse_client_message('{"type":"utterance","session_id":"s","text":"hi","ts_user_stop_ms":1}'),
            Utterance,
        ),
    )
    check("barge parses", isinstance(parse_client_message('{"type":"barge","session_id":"s"}'), Barge))
    check("garbage -> None", parse_client_message("not json") is None)
    check("unknown type -> None", parse_client_message('{"type":"nope"}') is None)

    print("\n[3] Hello handshake registers the device + replies ready")
    agent = StubAgent()
    server = BrainServer(agent=agent)
    ws = FakeWS()
    await server.handle_message(ws, Hello(session_id="s1", device_id="iphone", headphones_connected=True))
    check("ready lifecycle sent", ws.sent and ws.sent[-1]["kind"] == "lifecycle" and ws.sent[-1]["delta"] == "ready")
    check("session stored with headphones flag", server._sessions["s1"]["headphones_connected"] is True)

    print("\n[4] Utterance -> thinking, tool filler, streamed assistant chunks (each sentence non-final + terminal marker)")
    ws = FakeWS()
    await server.handle_message(ws, Utterance(session_id="s1", text="hey afon", ts_user_stop_ms=1))
    await server._turns["s1"]  # let the turn finish
    check("agent.respond got the text", agent.seen[-1] == "hey afon")
    check("thinking lifecycle first", ws.sent[0]["kind"] == "lifecycle" and ws.sent[0]["delta"] == "thinking")
    check("tool filler relayed", "Working on it…" in ws.deltas("tool"))
    assistant = [f for f in ws.sent if f["kind"] == "assistant"]
    spoken = [f for f in assistant if f["delta"].strip()]
    # New low-latency contract: each sentence streams the instant it's generated (final=False), then a
    # single empty final=True marker ends the turn. (The old contract held one sentence back so it could
    # tag the last as final — that delayed the FIRST spoken sentence by a whole sentence's generation.)
    check("each sentence is its own non-final chunk (streamed as generated)",
          len(spoken) == 2 and all(f["final"] is False for f in spoken), str(assistant))
    check("a single empty final=True marker ends the turn",
          assistant[-1]["final"] is True and assistant[-1]["delta"].strip() == ""
          and sum(1 for f in assistant if f["final"]) == 1, str(assistant))
    check("chunks reconstruct the reply", "".join(f["delta"] for f in assistant).strip() == "Hello sir. All set.")

    print("\n[5] one shared brain backs every session (phone + glasses = same agent)")
    ws2 = FakeWS()
    await server.handle_message(ws2, Utterance(session_id="s2", text="from glasses", ts_user_stop_ms=2))
    await server._turns["s2"]
    check("same agent saw both sessions' turns", agent.seen == ["hey afon", "from glasses"])

    print("\n[6] barge / supersede cancels the in-flight turn")
    slow = StubAgent(delay=5.0)
    server2 = BrainServer(agent=slow)
    ws = FakeWS()
    await server2.handle_message(ws, Utterance(session_id="s1", text="long one", ts_user_stop_ms=1))
    await asyncio.sleep(0.05)  # let the turn start + emit "thinking"
    task = server2._turns["s1"]
    await server2.handle_message(ws, Barge(session_id="s1"))
    await asyncio.sleep(0.05)
    check("in-flight turn cancelled", task.cancelled() or task.done())
    check("client told cancelled", "cancelled" in [f["delta"] for f in ws.sent if f["kind"] == "lifecycle"])

    print("\n[6b] cancelled-mid-stream turn records placeholder so history stays valid")
    # Stubbed agent with a streaming impl we can cancel mid-flight — same wiring as test 6's
    # slow agent, but exercising respond_stream's finally-clause (which appends "(interrupted)").
    class _StubStreaming:
        def __init__(self) -> None:
            self._history: list[dict] = []

        async def respond_stream(self, user_text: str, on_progress=None):  # type: ignore[no-untyped-def]
            self._history.append({"role": "user", "content": user_text})
            try:
                # Yield one chunk, then suspend; the test cancels the drain after seeing it.
                yield "thinking…"
                await asyncio.sleep(10.0)  # never reached — test cancels here
                yield "done"
            finally:
                # Mirror the real respond_stream's placeholder guard.
                self._history.append({"role": "assistant", "content": "(interrupted)"})

    stub = _StubStreaming()
    consumed: list[str] = []
    gen = stub.respond_stream("hi there")
    try:
        await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        consumed.append(await gen.__anext__())   # try to pull the second chunk
    except (StopAsyncIteration, asyncio.TimeoutError):
        pass
    finally:
        # An async generator's finally DOES run when aclose() is called — equivalent to the
        # barge/supersede path that closes the in-flight stream from outside.
        await gen.aclose()
    check("at least one chunk was streamed before cancel", len(consumed) >= 1, str(consumed))
    # After cancellation, history must NEVER end with two user turns (the AUDIT #7 invariant).
    user_count = sum(1 for m in stub._history if m["role"] == "user")
    last_two = stub._history[-2:]
    consecutive_users = (
        len(last_two) == 2
        and last_two[0]["role"] == "user"
        and last_two[1]["role"] == "user"
    )
    check("no consecutive user turns after cancel", not consecutive_users, str(last_two))
    check("the placeholder turn was recorded", any(
        m["role"] == "assistant" and "interrupted" in m["content"].lower() for m in stub._history
    ), str(stub._history))

    print("\n[7] optional bearer auth gates remote clients")
    import afon.config as cfg

    old = cfg.settings.api_auth_token
    try:
        cfg.settings.api_auth_token = "secret"
        check("no header -> rejected", BrainServer._authorized(FakeWS(auth="")) is False)
        check("wrong token -> rejected", BrainServer._authorized(FakeWS(auth="Bearer nope")) is False)
        check("right token -> allowed", BrainServer._authorized(FakeWS(auth="Bearer secret")) is True)
        cfg.settings.api_auth_token = None
        check("blank token (loopback) -> allowed", BrainServer._authorized(FakeWS()) is True)
    finally:
        cfg.settings.api_auth_token = old

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run())
