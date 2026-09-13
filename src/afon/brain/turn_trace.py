"""One row per turn — what Afon decided, what it cost, and where the time went (SYSTEMS.md 01.F3).

Every tuning item in Wave 1 and half of Wave 2 is phrased as a number to beat: the 58-tool /
~10k-token prefill (03.F3), p50 to first audio (S01 budget), recall p95 (S30). None of those
numbers exist today. `METRICS` counts turns and tool calls in memory, and the brain restarts
daily, so the evidence for "which turns are expensive and why" was being thrown away every night.

A row carries exactly what 01.F3 names — intent class, tools considered, tools fired, prefill
tokens, wall clock per stage — and is written for **every** turn, including the ones that refuse
early, take the deterministic zero-arg fast path, or raise. That last part is the whole point: the
turns that end unusually are the ones worth seeing, and they are precisely the ones an
emit-on-success tracer drops. So the row is emitted from a `finally`, and `REQUIRED` below is the
contract a row must satisfy to count as complete.

Rows live in the current turn's context, so a helper deep in the tool path records into the right
turn without every signature growing a parameter. Fail-quiet throughout: a tracer that can break a
turn is worse than no tracer.

    from afon.brain.turn_trace import turn, current
    with turn("what's the weather?") as t:
        t.set_intent("lookup"); ...

ponytail: token counts are chars/4, not tiktoken. That is ±15% — plenty to watch 10k fall to 2.5k,
not enough to bill against. If 02.R1 (money per intent class) lands, swap `_tokens` for a real
encoder; nothing else here changes.
"""

from __future__ import annotations

import contextvars
import json
import os
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from loguru import logger
from afon.shared.paths import state_dir

#: A row is COMPLETE only if it carries all of these. Each maps to something 01.F3 names:
#: intent class, tools considered (count + what they cost), tools fired, prefill tokens,
#: wall clock per stage. `test_metrics.py` asserts this mapping, so dropping a field from the
#: row fails the gate rather than quietly shrinking what the tuning work can see.
REQUIRED = ("ts", "intent", "tools_considered", "catalogue_tokens", "prefill_tokens",
            "tools_fired", "stages_ms", "total_ms", "ok")

#: The classes S01.R1 will have to make explicit and testable. The floor derives what the existing
#: routing signals already decide and labels the rest "general" — deriving "ambiguous" needs the
#: classifier R1 builds, and inventing it here would put a guess in the evidence.
INTENTS = ("chat", "lookup", "act", "multi", "general", "refused")

#: Tool-name prefixes that CHANGE something. A narrowed turn is a lookup unless its one tool is
#: one of these. Deliberately the inverse of an allowlist of read verbs: most of the 142 tools are
#: reads named after their subject (`weather`, `crypto_price`, `now_playing`), so an allowlist has
#: to grow forever and mislabels every tool it has not met yet, while the set of verbs that mutate
#: the world is short and closed.
_WRITE_VERBS = frozenset({"add", "cancel", "complete", "create", "delete", "execute", "forget",
                          "invoke", "mark", "media", "move", "open", "play", "plan", "post",
                          "process", "remember", "remove", "run", "save", "schedule", "send",
                          "set", "stop", "update", "work", "write"})

KEEP_DAYS = 14

_RING: deque[dict] = deque(maxlen=200)
_LOCK = threading.Lock()
_CURRENT: contextvars.ContextVar["TurnTrace | None"] = contextvars.ContextVar(
    "afon_turn_trace", default=None)
_SCHEMA_TOKENS: dict[str, int] = {}
_pruned = False
_seq = 0


def trace_dir() -> Path:
    """Where rows land. Env-overridable so tests never write into the owner's real history."""
    return Path(os.environ.get("AFON_TRACE_DIR") or (state_dir() / "traces"))


def _tokens(text: str) -> int:
    return (len(text) + 3) // 4


def catalogue_tokens(tools: list[dict[str, Any]] | None) -> int:
    """Prefill cost of the presented tool catalogue — the number 03.F3 exists to drive down.

    Cached per tool name: the schemas are module-level constants that never change at runtime, and
    re-serialising ~58 of them on every turn would put JSON encoding in the latency path of the
    thing it is measuring.
    """
    if not tools:
        return 0
    total = 0
    for t in tools:
        try:
            name = t["function"]["name"]
        except (KeyError, TypeError):
            name = None
        if name is None:
            total += _tokens(json.dumps(t, separators=(",", ":"), default=str))
            continue
        cost = _SCHEMA_TOKENS.get(name)
        if cost is None:
            cost = _SCHEMA_TOKENS[name] = _tokens(json.dumps(t, separators=(",", ":"), default=str))
        total += cost
    return total


def message_tokens(messages: list[dict[str, Any]] | None) -> int:
    """Prefill cost of the message list. +4 per message for the role/delimiter framing every
    chat format adds."""
    if not messages:
        return 0
    total = 0
    for m in messages:
        c = m.get("content")
        total += 4 + _tokens(c if isinstance(c, str) else json.dumps(c, default=str))
    return total


def classify(*, pure_chat: bool = False, multi_intent: bool = False, work_intent: bool = False,
             narrowed: bool = False, forced_name: str | None = None) -> str:
    """Name the turn from the signals the router already computed.

    Order mirrors `_prepare_turn`: a work intent takes precedence over the multi-intent nudge
    there, so it must here too, or the trace would disagree with the turn it describes.
    """
    if work_intent:
        return "act"
    if multi_intent:
        return "multi"
    if pure_chat:
        return "chat"
    if narrowed and forced_name:
        return "act" if forced_name.split("_", 1)[0] in _WRITE_VERBS else "lookup"
    return "general"


@dataclass
class TurnTrace:
    """One turn's row. Mutated as the turn proceeds; frozen into a dict when it closes."""

    text: str = ""
    streamed: bool = False
    intent: str = "general"
    language: str = ""
    tools_considered: int = 0
    catalogue_tokens: int = 0
    prefill_tokens: int = 0
    tools_fired: list[str] = field(default_factory=list)
    stages_ms: dict[str, float] = field(default_factory=dict)
    reached: list[str] = field(default_factory=list)
    ok: bool = True
    error: str | None = None
    #: 45.F3 — what the answer RESTED on, and how firmly it was put. The trace already knew which
    #: tools fired and how long each stage took, which answers "what did you do" and answers
    #: nothing about "should I believe it". Sources come from the turn's citation ledger; the
    #: confidence label is read off the reply itself rather than invented, because a number Afon
    #: made up about his own certainty is the least trustworthy thing in the row.
    sources: list[str] = field(default_factory=list)
    confidence: str = "unstated"
    seq: int = 0
    _t0: float = field(default_factory=time.perf_counter)

    def set_intent(self, intent: str) -> None:
        self.intent = intent if intent in INTENTS else "general"

    def note_prompt(self, messages: list[dict] | None, tools: list[dict] | None) -> None:
        """Record what the first model pass will actually be charged for."""
        self.tools_considered = len(tools or [])
        self.catalogue_tokens = catalogue_tokens(tools)
        self.prefill_tokens = message_tokens(messages) + self.catalogue_tokens

    def fired(self, name: str) -> None:
        if name:
            self.tools_fired.append(name)

    def note_answer(self, reply: str) -> None:
        """Close the row's account of WHY: what was read, and how firmly the answer was put."""
        try:
            from afon.shared.uncertainty import hedged

            self.confidence = "hedged" if hedged(reply or "") else "flat"
        except Exception as e:  # noqa: BLE001 — an unlabelled turn is better than a lost one
            logger.debug(f"turn trace: confidence unavailable ({type(e).__name__})")
        try:
            from afon.brain import citations

            self.sources = sorted(citations.hosts())
        except Exception as e:  # noqa: BLE001
            logger.debug(f"turn trace: sources unavailable ({type(e).__name__})")

    def add_ms(self, stage: str, ms: float) -> None:
        """Accumulate, don't overwrite — the model stage runs once per tool-resolution pass, and a
        turn that took four passes is exactly the turn this is meant to expose."""
        self.stages_ms[stage] = round(self.stages_ms.get(stage, 0.0) + ms, 1)
        if stage not in self.reached:
            self.reached.append(stage)

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        t = time.perf_counter()
        try:
            yield
        finally:
            self.add_ms(name, (time.perf_counter() - t) * 1000.0)

    def row(self) -> dict:
        return {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "seq": self.seq,
            "intent": self.intent,
            "language": self.language,
            "streamed": self.streamed,
            "tools_considered": self.tools_considered,
            "catalogue_tokens": self.catalogue_tokens,
            "prefill_tokens": self.prefill_tokens,
            "tools_fired": list(self.tools_fired),
            "sources": list(self.sources),
            "confidence": self.confidence,
            "stages_ms": dict(self.stages_ms),
            "reached": list(self.reached),
            "total_ms": round((time.perf_counter() - self._t0) * 1000.0, 1),
            "ok": self.ok,
            "error": self.error,
        }


def is_complete(row: dict) -> bool:
    """A row counts only if every field 01.F3 asks for is present and typed. `None` in any of them
    means the turn ended somewhere the tracer does not actually cover."""
    if any(k not in row or row[k] is None for k in REQUIRED):
        return False
    return (isinstance(row["tools_fired"], list) and isinstance(row["stages_ms"], dict)
            and row["intent"] in INTENTS)


def current() -> TurnTrace | None:
    """The turn being traced on this task, or None outside a turn."""
    return _CURRENT.get()


def fired(name: str) -> None:
    """Record a tool firing against the current turn, if there is one. Safe anywhere."""
    t = _CURRENT.get()
    if t is not None:
        t.fired(name)


def add_stage(name: str, ms: float) -> None:
    """Add already-measured milliseconds to a stage of the current turn. For callers that time
    themselves anyway — the LLM chain measures its own route latency across failovers, and
    re-timing it from outside would only produce a second, slightly different number."""
    t = _CURRENT.get()
    if t is not None:
        t.add_ms(name, ms)


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Time a stage of the current turn. A no-op outside a turn, so instrumented helpers stay
    callable from crons and tests."""
    t = _CURRENT.get()
    if t is None:
        yield
        return
    with t.stage(name):
        yield


@contextmanager
def turn(user_text: str = "", streamed: bool = False) -> Iterator[TurnTrace]:
    """Trace one turn. The row is emitted from `finally`, so a turn that refuses early, returns on
    the fast path, or raises still leaves evidence — those are the turns worth having."""
    global _seq
    with _LOCK:
        _seq += 1
        seq = _seq
    t = TurnTrace(text=user_text[:120], streamed=streamed, seq=seq)
    token = _CURRENT.set(t)
    try:
        yield t
    except BaseException as e:   # noqa: BLE001 — recorded, then re-raised untouched
        t.ok = False
        t.error = f"{type(e).__name__}: {e}"[:200]
        raise
    finally:
        try:
            _CURRENT.reset(token)
        except ValueError:
            # An abandoned async generator is closed by the event loop, in a different context
            # from the one that opened it, and the token will not reset there. The row still has
            # to be written — a barge-in is exactly the turn worth seeing.
            _CURRENT.set(None)
        _emit(t.row())


def last() -> dict | None:
    """The most recently CLOSED turn, or None. 45.F3 — "why" is a question about the one before."""
    with _LOCK:
        return dict(_RING[-1]) if _RING else None


def note_answer(reply: str) -> None:
    """Record what the turn in flight actually answered. No-op outside a turn."""
    t = _CURRENT.get()
    if t is not None:
        t.note_answer(reply)


def _emit(row: dict) -> None:
    with _LOCK:
        _RING.append(row)
    try:
        _append(row)
    except Exception as e:  # noqa: BLE001 — a trace that can break a turn is worse than no trace
        logger.debug(f"turn trace not persisted: {type(e).__name__}: {e}")
    from afon.brain.metrics import METRICS
    METRICS.observe("turn_total_ms", row["total_ms"])
    METRICS.observe("turn_prefill_tokens", float(row["prefill_tokens"]))
    METRICS.incr(f"intent.{row['intent']}")


def _append(row: dict) -> None:
    global _pruned
    d = trace_dir()
    d.mkdir(parents=True, exist_ok=True)
    if not _pruned:
        _pruned = True
        _prune(d)
    path = d / f"turns-{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def _prune(d: Path) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for p in d.glob("turns-*.jsonl"):
        if p.stem[len("turns-"):] < cutoff:
            p.unlink(missing_ok=True)


def recent(n: int = 20) -> list[dict]:
    with _LOCK:
        return list(_RING)[-n:]


def summary(n: int = 50) -> dict:
    """What the HUD shows: are rows complete, and what do the last n turns cost?"""
    rows = recent(n)
    if not rows:
        return {"turns": 0, "complete": 0, "incomplete": 0}
    bad = [r for r in rows if not is_complete(r)]
    prefills = sorted(r["prefill_tokens"] for r in rows)
    totals = sorted(r["total_ms"] for r in rows)
    mid = lambda s: s[len(s) // 2]  # noqa: E731
    by_intent: dict[str, int] = {}
    for r in rows:
        by_intent[r["intent"]] = by_intent.get(r["intent"], 0) + 1
    return {
        "turns": len(rows),
        "complete": len(rows) - len(bad),
        "incomplete": len(bad),
        "p50_prefill_tokens": mid(prefills),
        "max_prefill_tokens": prefills[-1],
        "p50_total_ms": mid(totals),
        "by_intent": by_intent,
        "failed": sum(1 for r in rows if not r["ok"]),
    }


def load(days: int = 3) -> list[dict]:
    """Rows from the last `days` files — the evidence 01.F3 asks to collect before tuning."""
    out: list[dict] = []
    d = trace_dir()
    if not d.is_dir():
        return out
    for p in sorted(d.glob("turns-*.jsonl"))[-days:]:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _selfcheck() -> None:
    """python -m afon.brain.turn_trace — the smallest thing that fails if the row contract breaks."""
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        os.environ["AFON_TRACE_DIR"] = td
        with turn("hello", streamed=False) as t:
            t.set_intent(classify(pure_chat=True))
            t.note_prompt([{"role": "user", "content": "hello"}], [])
            with t.stage("llm"):
                pass
        try:
            with turn("boom") as t:
                t.note_prompt([], [])
                raise ValueError("x")
        except ValueError:
            pass
        rows = load(1)
        assert len(rows) == 2, rows
        assert all(is_complete(r) for r in rows), rows
        assert rows[0]["intent"] == "chat"
        assert rows[1]["ok"] is False and "ValueError" in rows[1]["error"]
        assert current() is None
    os.environ.pop("AFON_TRACE_DIR", None)
    print("turn_trace selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
