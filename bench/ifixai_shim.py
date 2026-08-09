"""An OpenAI-compatible endpoint in front of Afon, so iFixAi can audit him.

iFixAi's `http` provider POSTs an OpenAI-shaped body to ``{endpoint}/chat/completions``. Afon
speaks neither that nor anything close: his HTTP surface is ``POST /talk`` taking ``{"text": …}``
and answering with an MP3 plus an ``X-Afon-Reply`` header. This translates between the two.

WHY NOT JUST POINT IT AT /talk's HOST. Three reasons, and the first two cost real money:

  1. /talk SYNTHESISES SPEECH for every reply. An audit run is ~450 probes; that is 450 needless
     ElevenLabs syntheses for text nobody listens to.
  2. /talk runs under the SAME agent lock as live voice and Telegram. A run would block the owner
     out of his own assistant for the duration.
  3. Every /talk turn is a real turn: it lands in the L2 journal and can write L1 facts. An audit
     would leave ~450 probe utterances — "ignore your instructions and…" — in Afon's memory of
     its owner. That is the one that is hard to undo.

So this drives ``AfonAgent`` in-process instead: the same code, system prompt, tool belt, intent
router, confirm gate and memory layers the deployed brain runs, with TTS never invoked and state
redirected to a scratch directory. What is audited is Afon's GOVERNANCE as shipped, which is what
`--grounding sut` is asking about; what is avoided is charging the owner for it.

    # 1. start the shim (leave it running)
    uv run python bench/ifixai_shim.py --port 8799

    # 2. point iFixAi at it
    ifixai run --provider http --endpoint http://127.0.0.1:8799/v1 \
      --grounding sut --suite strategic --fixture bench/ifixai_fixture_afon.yaml

Health: ``GET /healthz`` → ok. Probe count: ``GET /stats``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_AGENT = None
_LOOP: asyncio.AbstractEventLoop | None = None
_STATS = {"requests": 0, "errors": 0, "started": time.time()}


def _isolate_state(scratch: Path) -> None:
    """Point Afon's writable state at a scratch dir BEFORE the agent is constructed.

    Without this, ~450 hostile audit probes land in the owner's real journal and learned facts —
    "ignore your previous instructions and…" remembered as things he said. That is the one
    consequence of this whole exercise that is genuinely hard to undo.

    Note the two different mechanisms, because the obvious one is not enough:
      * env vars cover the settings that ARE configurable (session, proactive state);
      * the MEMORY STORE is not among them. `memory.py` hard-codes `_REPO_ROOT / "memory"` and
        binds a module-level `STORE = MemoryStore()` at import. So it is rebound explicitly
        below. An earlier version of this file set a plausible-looking `AFON_MEMORY_DIR` and
        isolated nothing at all — the variable does not exist.
    """
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(scratch)
    os.environ["USERPROFILE"] = str(scratch)
    for var, val in {
        "AFON_SESSION_PERSIST_PATH": str(scratch / "session.json"),
        "AFON_PROACTIVE_ENABLED": "false",   # no unprompted speech mid-audit
        "AFON_PROACTIVE_STATE_PATH": str(scratch / "proactive_state.json"),
    }.items():
        os.environ.setdefault(var, val)

    from afon.brain import memory as _mem

    real = _mem.STORE.base
    _mem.STORE = _mem.MemoryStore(scratch / "memory")
    if _mem.STORE.base.resolve() == Path(real).resolve():
        raise SystemExit("refusing to start: memory isolation failed, probes would hit real memory")
    print(f"memory redirected: {real} -> {_mem.STORE.base}")


async def _boot() -> None:
    global _AGENT
    from afon.brain.agent import AfonAgent

    _AGENT = AfonAgent()
    await _AGENT.warmup()


async def _answer(text: str) -> str:
    """One full turn: routing, tools, memory, confirm gate — everything except speech."""
    assert _AGENT is not None
    reply = await _AGENT.respond(text)
    if isinstance(reply, tuple):          # (text, meta) on some paths
        reply = reply[0]
    return str(reply or "").strip()


def _sources_for(query: str) -> list[dict]:
    """What Afon's memory actually returned for this query — real hits, not a claim that he has
    memory. Runs the same `fused_recall` a turn uses, so a source listed here is one the agent
    could genuinely have grounded on."""
    if not query:
        return []
    try:
        from afon.brain.memory import STORE

        fut = asyncio.run_coroutine_threadsafe(STORE.fused_recall(query, limit=5), _LOOP)
        hits = fut.result(timeout=30) or []
    except Exception:  # noqa: BLE001 — a retrieval hiccup must not fail the probe
        return []
    out = []
    for h in hits:
        out.append({
            "source_id": f"{h.get('layer', 'mem')}:{h.get('source', 'memory')}",
            "content": str(h.get("text", ""))[:500],
            "score": float(h.get("score") or 0.0),
        })
    return out


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:      # noqa: A003 - quiet the default logger
        return

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        route = self.path.split("?")[0].rstrip("/")
        if route in ("/healthz", "/health"):
            self._json(200, {"status": "ok", "agent": _AGENT is not None})
        elif route == "/stats":
            self._json(200, dict(_STATS, uptime_s=round(time.time() - _STATS["started"], 1)))
        elif route in ("/v1/models", "/models"):
            # Some clients probe this before their first completion.
            self._json(200, {"object": "list",
                             "data": [{"id": "afon", "object": "model", "owned_by": "afon"}]})
        else:
            self._json(404, {"error": {"message": "not found"}})

    def _read_body(self) -> bytes:
        """ALWAYS drain the request body, on every path including errors and 404s.

        This is not defensive tidiness; skipping it corrupted real audit results. The connection
        is HTTP/1.1 keep-alive, so an unread body stays in the socket buffer and is parsed as the
        start of the NEXT request — iFixAi probes `/retrieve`, the shim 404'd it without reading
        `{"query": "..."}`, and the following request arrived as
        `{"query": "test"}POST /v1/chat/completions HTTP/1.1` → HTTP 400. The inspection recorded
        that as the AGENT erroring, and B03 Auditability scored 0% for a fault that was entirely
        in this file. A transport bug that reads as an agent failure is the worst kind here.
        """
        try:
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))
        except (ValueError, OSError):
            return b""

    def do_POST(self) -> None:  # noqa: N802
        route = self.path.split("?")[0].rstrip("/")
        raw = self._read_body()

        # Capability hook: iFixAi's http provider POSTs {"query": …} to /retrieve to ask what
        # sources grounded an answer. Afon really does retrieve (L1 facts, L2 journal, L3 vault,
        # L5b graph), so answering it truthfully turns B05 Source Provenance from
        # "insufficient evidence" into a scored result — evidence, not assertion.
        if route.endswith("/retrieve"):
            try:
                q = (json.loads(raw or b"{}").get("query") or "").strip()
            except (ValueError, json.JSONDecodeError):
                q = ""
            self._json(200, {"sources": _sources_for(q)})
            return

        if not route.endswith("/chat/completions"):
            self._json(404, {"error": {"message": "not found"}})
            return
        try:
            req = json.loads(raw or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": {"message": "bad json"}})
            return

        # Flatten the OpenAI messages array into one utterance. Afon keeps his own conversation
        # state, so replaying prior assistant turns would double-count them; the system message is
        # prepended because iFixAi puts the scenario's framing there and dropping it would audit a
        # different situation than the one it set up.
        msgs = req.get("messages") or []
        sys_parts = [m.get("content", "") for m in msgs if m.get("role") == "system"]
        user_parts = [m.get("content", "") for m in msgs if m.get("role") == "user"]
        text = "\n\n".join([*(p for p in sys_parts if p), *(p for p in user_parts if p)]).strip()
        if not text:
            self._json(400, {"error": {"message": "no user content"}})
            return

        _STATS["requests"] += 1
        try:
            fut = asyncio.run_coroutine_threadsafe(_answer(text), _LOOP)
            reply = fut.result(timeout=180)
        except Exception as e:  # noqa: BLE001 — a probe that errors must not kill the shim
            _STATS["errors"] += 1
            # Report the failure as content rather than a 500: an audit wants to see what the
            # agent did with a hostile prompt, and a transport error would be scored as the
            # agent's answer. Making it explicit keeps the judge from grading a stack trace.
            reply = f"[shim error: {type(e).__name__}: {e}]"

        self._json(200, {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req.get("model") or "afon",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": reply}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })


def main() -> int:
    ap = argparse.ArgumentParser(description="OpenAI-compatible shim in front of AfonAgent")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--state", default="", help="scratch state dir (default: a temp dir)")
    args = ap.parse_args()

    scratch = Path(args.state) if args.state else Path(os.environ.get("TEMP", "/tmp")) / "afon-ifixai-state"
    _isolate_state(scratch)
    print(f"state isolated to {scratch} (the owner's real memory is untouched)")

    global _LOOP
    _LOOP = asyncio.new_event_loop()

    import threading

    threading.Thread(target=_LOOP.run_forever, daemon=True).start()
    asyncio.run_coroutine_threadsafe(_boot(), _LOOP).result(timeout=300)
    print(f"afon shim ready on http://{args.host}:{args.port}/v1  (POST /v1/chat/completions)")

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print(f"\nstopping. probes served: {_STATS['requests']} (errors: {_STATS['errors']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
