"""End-to-end error tracking across every Afon process.

The problem this solves: Afon runs as THREE processes on TWO machines — the voice edge and the
pc_agent on the laptop, the brain on the VPS — and until now each one logged into its own silo. A
single spoken turn crosses all three (wake -> STT -> speaker gate -> brain -> tools/LLM -> TTS), so
when it went wrong there was no way to line the laptop's log up against the VPS's, and anything the
owner experienced was invisible from the brain side. Worse, the codebase had exactly one log sink
and no exception hooks at all, so an unhandled error inside a background task simply vanished.

Three ideas, no more:

  1. **One sink catches everything.** A loguru sink at WARNING+ writes every warning, error and
     exception to a structured journal. Nothing needs instrumenting at the call site — code that
     already does ``logger.warning(...)`` is covered, including third-party libraries (pipecat's
     WebSocket failures) and the standard ``logging`` module, which is bridged in.
  2. **A turn id ties the processes together.** ``new_turn()`` on the edge, carried to the brain on
     the wire, injected into every log line automatically. One id answers "show me everything that
     happened during the turn that went wrong", across machines.
  3. **The laptop ships its errors to the brain.** The brain's journal is therefore the union of all
     three processes, so one query from anywhere sees what the owner saw.

Non-negotiables, because this writes to disk and crosses the network:
  * **Secrets are scrubbed** before anything is stored — an exception message can easily contain an
    API key from a URL or header.
  * **It can never break a turn.** Every path is wrapped and fail-quiet, with a re-entrancy guard so
    a failure inside the tracker cannot log itself into a loop.
  * **It cannot grow without bound** — the journal is capped and rotated in place.
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from loguru import logger
from afon.shared.paths import state_dir

# --- where it lands ---------------------------------------------------------------------------
_DIR = state_dir()
JOURNAL = _DIR / "errors.jsonl"
# Successful operations are journalled too (owner's call), which is far more volume than failures
# alone — so the cap is generous. The oldest half is dropped on rotate, keeping tail latency flat.
_MAX_BYTES = 24_000_000
_KEEP_ON_ROTATE = 0.5

# "OK" is the level for a successful operation. It is a first-class entry — the full activity trail —
# but the failure-shaped views filter it out by default so a real problem is never buried in it.
OK = "OK"
_FAILURE_LEVELS = {"WARNING", "ERROR", "CRITICAL"}

# --- correlation -------------------------------------------------------------------------------
_turn: ContextVar[str] = ContextVar("afon_turn", default="")
_PROCESS = "unknown"
_HOST = socket.gethostname()

# A failure inside the sink must not be logged BY the sink. Without this, one bad write becomes an
# infinite loop that takes the process down — the opposite of what an error tracker is for.
_in_sink = threading.local()

# Set by install(); lets the edge hand its errors to the brain without importing the client here.
_shipper: Callable[[dict], None] | None = None


def _scrub(text: str) -> str:
    """Redact anything that looks like a credential. Exception text routinely carries them —
    an httpx error quotes the full URL including its ``?api_key=`` query, and auth headers show up
    in tracebacks. This is the last line of defence before the value hits disk or the network."""
    if not text:
        return text
    # key=value / "key": "value" / Bearer xyz / long opaque tokens
    text = re.sub(r"(?i)\b(api[-_]?key|token|secret|password|auth|credential|appid|app_id)"
                  r"(\"?\s*[:=]\s*\"?)([^\s\",&}]{4,})", r"\1\2<redacted>", text)
    text = re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}", "bearer <redacted>", text)
    text = re.sub(r"\b(sk|xi|dg|pk)-[A-Za-z0-9_\-]{12,}\b", r"\1-<redacted>", text)
    return text


def _subsystem(name: str) -> str:
    """Turn a logger name into the subsystem the owner would recognise.

    ``afon.edge.stt`` -> ``edge/stt``; ``afon.brain.tools.calendar`` -> ``brain/tools.calendar``.
    Third-party names keep their top package (``pipecat``, ``websockets``) — those failures are real
    and attributing them to 'unknown' would hide the cloud-voice problems we actually hit."""
    if not name:
        return "unknown"
    if name.startswith("afon."):
        parts = name.split(".")[1:]
        return f"{parts[0]}/{'.'.join(parts[1:])}" if len(parts) > 1 else parts[0]
    if name == "__main__":
        return f"{_PROCESS}/main"
    return name.split(".")[0]


# --- turn correlation --------------------------------------------------------------------------
def new_turn(turn_id: str | None = None) -> str:
    """Start a correlated turn and return its id. The edge calls this when the user stops speaking
    and passes the id to the brain, which adopts it — so both sides' logs share one key."""
    tid = turn_id or uuid.uuid4().hex[:8]
    _turn.set(tid)
    return tid


def current_turn() -> str:
    return _turn.get()


# --- the journal -------------------------------------------------------------------------------
def _rotate_if_needed() -> None:
    try:
        if not JOURNAL.exists() or JOURNAL.stat().st_size < _MAX_BYTES:
            return
        lines = JOURNAL.read_text(encoding="utf-8", errors="replace").splitlines()
        keep = lines[int(len(lines) * (1 - _KEEP_ON_ROTATE)):]
        JOURNAL.write_text("\n".join(keep) + "\n", encoding="utf-8")
    except OSError:
        pass


def record(
    *,
    level: str,
    subsystem: str,
    message: str,
    error_type: str = "",
    where: str = "",
    turn: str | None = None,
    process: str | None = None,
    host: str | None = None,
    context: dict[str, Any] | None = None,
    ship: bool = True,
) -> dict | None:
    """Append one structured error. Returns the entry (for tests), or None if it was dropped.

    Fail-quiet by contract: callers are logging a problem already, and raising here would turn an
    observability gap into an outage."""
    try:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "host": host or _HOST,
            "process": process or _PROCESS,
            "subsystem": subsystem,
            "level": level,
            "turn": turn if turn is not None else current_turn(),
            "type": _scrub(error_type)[:80],
            "message": _scrub(message)[:600],
            "where": where[:120],
        }
        if context:
            # Numbers stay numbers — scrubbing is for text, and stringifying duration_ms would make
            # "show me every operation slower than 30 seconds" impossible to ask.
            entry["context"] = {
                k: (v if isinstance(v, (int, float, bool)) else _scrub(str(v))[:200])
                for k, v in list(context.items())[:10]
            }
        _DIR.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed()
        with JOURNAL.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if ship and _shipper is not None:
            try:
                _shipper(entry)
            except Exception:  # noqa: BLE001 — shipping is best-effort; the local copy is written
                pass
        return entry
    except Exception:  # noqa: BLE001
        return None


def record_op(
    kind: str,
    name: str,
    *,
    ok: bool,
    detail: str = "",
    duration_ms: float | None = None,
    slow_ms: float | None = None,
    context: dict[str, Any] | None = None,
) -> dict | None:
    """Journal the OUTCOME of one operation — a tool call, a PC op, a signal source, a job.

    Exceptions are only half the story. Most of Afon's failures are *soft*: a tool catches its own
    error and returns a polite sentence, an integration reports "not configured", a signal source
    quietly yields nothing. Those never raise and never log, so they were invisible — which is
    exactly how a dead integration can look healthy for weeks. This records the outcome itself.

    Successes are journalled too, at level ``OK``. That gives a complete activity trail — what ran,
    when, how long it took, and whether it worked — which is what makes "show me everything that
    happened during that turn" answer the whole question rather than only its failures. The default
    query views hide OK entries so a real problem is never buried under routine noise; ``--all`` (or
    ``include_ok=True``) shows the full trail.

    A success slower than ``slow_ms`` is upgraded to a WARNING, because that is what the owner
    experiences as "Afon is being slow" even though nothing technically failed."""
    ctx = dict(context or {})
    if duration_ms is not None:
        ctx["duration_ms"] = int(duration_ms)
    if not ok:
        return record(level="ERROR", subsystem=f"{kind}/{name}",
                      message=detail or f"{kind} '{name}' failed", error_type="Failed", context=ctx)
    if slow_ms is not None and duration_ms is not None and duration_ms >= slow_ms:
        return record(level="WARNING", subsystem=f"{kind}/{name}",
                      message=f"{kind} '{name}' was slow: {int(duration_ms)}ms", error_type="Slow",
                      context=ctx)
    return record(level=OK, subsystem=f"{kind}/{name}",
                  message=detail or f"{kind} '{name}' ok", error_type="", context=ctx)


def swallowed(where: str, exc: BaseException, *, note: str = "") -> None:
    """Record an error that was caught and deliberately ignored.

    The codebase is full of fail-quiet handlers, and that is usually right — an optional cache write
    should not break a turn. But the same pattern hides the failures that matter: a proactive source
    that errors and returns nothing looks identical to one with nothing to say, which is how a whole
    capability can go dormant for weeks without a single visible symptom. Call this where the
    swallow could cost a capability; leave genuinely inconsequential ones alone."""
    record(level="WARNING", subsystem=f"swallowed/{where}",
           message=note or f"{type(exc).__name__}: {exc}", error_type=type(exc).__name__)


def looks_failed(result: Any) -> bool:
    """Is a tool's string return a failure/degraded note rather than an answer?

    Mirrors ``brain.tools.base.tool_failed`` but lives here so the edge and pc_agent (which do not
    import the brain's tool stack) can use the same judgement, and so the phrasing lives in one
    place. Deliberately conservative: a false positive would cry wolf on a working tool."""
    if result is None:
        return True
    text = str(result).strip().lower()
    if not text:
        return True
    return any(m in text for m in (
        "i couldn't complete the", "isn't configured yet", "is not configured",
        "that failed on the laptop", "unknown tool ", "hit an error",
    ))


def _sink(msg) -> None:
    """loguru sink: every WARNING+ record becomes a journal entry, automatically."""
    if getattr(_in_sink, "busy", False):
        return
    _in_sink.busy = True
    try:
        r = msg.record
        exc = r.get("exception")
        etype = exc.type.__name__ if exc and exc.type else ""
        record(
            level=r["level"].name,
            subsystem=_subsystem(r["name"]),
            message=r["message"],
            error_type=etype,
            where=f"{r['name']}:{r['function']}:{r['line']}",
            turn=str(r["extra"].get("turn") or current_turn()),
        )
    except Exception:  # noqa: BLE001
        pass
    finally:
        _in_sink.busy = False


def _patcher(record_dict: dict) -> None:
    """Stamp the active turn id onto every log line, so the human-readable logs are correlated too,
    not just the journal."""
    record_dict["extra"].setdefault("turn", current_turn())


# --- catching what nobody caught ----------------------------------------------------------------
def _install_hooks() -> None:
    def _excepthook(exc_type, exc, tb):
        record(level="CRITICAL", subsystem=f"{_PROCESS}/uncaught", message=str(exc),
               error_type=getattr(exc_type, "__name__", "Exception"), where="sys.excepthook")
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = _excepthook

    def _threadhook(args):
        record(level="CRITICAL", subsystem=f"{_PROCESS}/thread", message=str(args.exc_value),
               error_type=getattr(args.exc_type, "__name__", "Exception"),
               where=f"thread:{getattr(args.thread, 'name', '?')}")

    threading.excepthook = _threadhook


def install_asyncio_handler(loop) -> None:
    """Catch exceptions from fire-and-forget tasks. Afon is full of them — proactive signals,
    room checks, backlog workers — and by default asyncio prints those to stderr, which ``pythonw``
    discards entirely. This is where silent background failures were being lost."""
    def _handler(_loop, ctx):
        exc = ctx.get("exception")
        record(level="ERROR", subsystem=f"{_PROCESS}/asyncio",
               message=str(exc) if exc else str(ctx.get("message", "")),
               error_type=type(exc).__name__ if exc else "",
               where=str(ctx.get("future") or ctx.get("task") or "")[:120])
        _loop.default_exception_handler(ctx)

    try:
        loop.set_exception_handler(_handler)
    except Exception:  # noqa: BLE001
        pass


class _StdlibBridge:
    """Route the standard ``logging`` module into loguru so libraries that don't use loguru
    (httpx, websockets, apscheduler) are covered by the same sink."""

    def __init__(self) -> None:
        import logging

        self._logging = logging

    def emit(self, rec) -> None:  # pragma: no cover - exercised via logging
        try:
            level = logger.level(rec.levelname).name
        except ValueError:
            level = rec.levelno
        logger.bind(std=True).opt(depth=6, exception=rec.exc_info).log(level, rec.getMessage())


def _bridge_stdlib() -> None:
    import logging

    class Handler(logging.Handler):
        _b = _StdlibBridge()

        def emit(self, rec):
            try:
                self._b.emit(rec)
            except Exception:  # noqa: BLE001
                pass

    root = logging.getLogger()
    if not any(isinstance(h, Handler) for h in root.handlers):
        root.addHandler(Handler(level=logging.WARNING))


def install(process: str, *, shipper: Callable[[dict], None] | None = None,
            bridge_stdlib: bool = True) -> None:
    """Turn on error tracking for this process. Idempotent; safe to call from any entrypoint."""
    global _PROCESS, _shipper
    _PROCESS = process
    if shipper is not None:
        _shipper = shipper
    try:
        logger.configure(patcher=_patcher)
        logger.add(_sink, level="WARNING", enqueue=False, backtrace=False, diagnose=False)
        _install_hooks()
        if bridge_stdlib:
            _bridge_stdlib()
        logger.info(f"error tracking on ({process}) -> {JOURNAL}")
    except Exception as e:  # noqa: BLE001 — never block startup on observability
        logger.warning(f"error tracking failed to install: {type(e).__name__}: {e}")


def set_shipper(fn: Callable[[dict], None] | None) -> None:
    """Point the journal at a transport (the edge uses the brain WebSocket)."""
    global _shipper
    _shipper = fn


# --- reading it back ----------------------------------------------------------------------------
def read(limit: int = 200, since_minutes: int | None = None, subsystem: str = "",
         turn: str = "", level: str = "", include_ok: bool = False) -> list[dict]:
    """Most recent entries first, optionally filtered. This is what every query surface uses.

    ``include_ok`` adds successful operations — the full activity trail. It defaults to off so the
    failure views stay sharp; tracing one turn (``turn=``) turns it on at the call site, because
    there the successes are the story: they show how far the turn got before it went wrong."""
    try:
        lines = JOURNAL.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    cutoff = time.time() - since_minutes * 60 if since_minutes else None
    out: list[dict] = []
    for line in reversed(lines):
        if len(out) >= limit:
            break
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not include_ok and e.get("level") == OK and not level:
            continue
        if subsystem and subsystem.lower() not in e.get("subsystem", "").lower():
            continue
        if turn and e.get("turn") != turn:
            continue
        if level and e.get("level", "").upper() != level.upper():
            continue
        if cutoff is not None:
            try:
                if datetime.fromisoformat(e["ts"]).timestamp() < cutoff:
                    break        # file is append-ordered, so everything older follows
            except (ValueError, KeyError):
                pass
        out.append(e)
    return out


def summary(since_minutes: int = 60) -> dict:
    """What is broken right now, grouped so the worst offender is obvious.

    ``total`` counts FAILURES, deliberately — it is the number the owner is asking about when he says
    "is anything wrong?". The successful operations are counted separately as ``ok`` so the same view
    also answers "how much did you actually do?", and a failure rate is derivable from the two."""
    entries = read(limit=8000, since_minutes=since_minutes, include_ok=True)
    by_sub: dict[str, int] = {}
    by_level: dict[str, int] = {}
    ok_count = 0
    failures: list[dict] = []
    for e in entries:
        lvl = e.get("level", "?")
        by_level[lvl] = by_level.get(lvl, 0) + 1
        if lvl == OK:
            ok_count += 1
            continue
        failures.append(e)
        by_sub[e.get("subsystem", "?")] = by_sub.get(e.get("subsystem", "?"), 0) + 1
    worst = sorted(by_sub.items(), key=lambda kv: -kv[1])
    return {
        "window_minutes": since_minutes,
        "total": len(failures),
        "ok": ok_count,
        "operations": ok_count + len(failures),
        "by_level": by_level,
        "by_subsystem": dict(worst),
        "top": worst[0][0] if worst else "",
        "latest": failures[0] if failures else None,
    }


if __name__ == "__main__":
    # Self-check: scrubbing, capture via loguru, correlation, filtering, and the recursion guard.
    import tempfile

    _DIR = Path(tempfile.mkdtemp())
    JOURNAL = _DIR / "errors.jsonl"

    assert "<redacted>" in _scrub("api_key=abcdef123456")
    assert "<redacted>" in _scrub('{"token": "abcdef123456"}')
    assert "<redacted>" in _scrub("Authorization: Bearer abcdefghijklmnop")
    assert "abcdef123456" not in _scrub("api_key=abcdef123456")
    assert _subsystem("afon.edge.stt") == "edge/stt"
    assert _subsystem("afon.brain.tools.calendar") == "brain/tools.calendar"
    assert _subsystem("pipecat.services.elevenlabs") == "pipecat"

    install("selftest", bridge_stdlib=False)
    tid = new_turn()
    logger.warning("a test warning with api_key=supersecretvalue")
    entries = read(limit=5)
    assert entries, "the sink must journal a warning"
    assert entries[0]["turn"] == tid, "entries carry the turn id"
    assert entries[0]["subsystem"].endswith("main") or "/" in entries[0]["subsystem"]
    assert "supersecretvalue" not in entries[0]["message"], "secrets must never reach the journal"

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        logger.exception("handler blew up")
    assert any(e["type"] == "RuntimeError" for e in read(limit=5)), "exception type is captured"

    other = new_turn()
    logger.error("second turn problem")
    assert len(read(turn=other)) == 1, "filtering by turn isolates one turn"
    assert summary(since_minutes=60)["total"] >= 3

    shipped: list[dict] = []
    set_shipper(shipped.append)
    logger.warning("ship me")
    assert shipped and shipped[-1]["message"] == "ship me", "entries are handed to the transport"
    set_shipper(None)

    # A sink that explodes must not recurse or raise into the caller.
    _in_sink.busy = True
    logger.warning("must be ignored while the sink is busy")
    _in_sink.busy = False

    print("errors self-check OK")
