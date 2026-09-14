"""Durable per-tool call counts — the data the catalogue-tiering work depends on.

`METRICS.incr(f"tool.{name}")` already counted every call, but only in memory, and the brain
restarts daily (the 01:00 refresh timer). So the question the tiering item is built on — *which
tools does the owner actually use?* — had no answer, and never would have: every restart threw
the evidence away. Tiering the tool catalogue without it means guessing which tools are rare, and
a wrong guess silently removes a capability rather than failing loudly.

Deliberately a flat JSON file, not SQLite: one dict of small ints, read once per process and
written on a debounce. A table would need a schema, a migration and a connection for data that
fits in a few KB.

    from afon.brain.tool_usage import USAGE
    USAGE.record("weather")
    USAGE.top(10)        # [("weather", 41), ...] most-used first
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

from loguru import logger
from afon.shared.paths import state_dir

_FLUSH_EVERY_S = 60.0


def _default_path() -> Path:
    return state_dir() / "tool_usage.json"


class ToolUsage:
    """Call counts per tool name, persisted across restarts. Thread-safe, fail-quiet."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _default_path()
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self._last: dict[str, str] = {}
        self._dirty = False
        # 0.0, not time.monotonic(): the FIRST record of a process flushes immediately, and only
        # then does the debounce start. Deliberate — a brain that dies early in its life still
        # leaves evidence behind, and the cost is one small write per process, once.
        self._last_flush = 0.0
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — missing/corrupt = start counting from zero
            return
        tools = data.get("tools") or {}
        for name, rec in tools.items():
            try:
                self._counts[name] = int(rec.get("calls") or 0)
                if rec.get("last"):
                    self._last[name] = str(rec["last"])
            except (ValueError, TypeError):
                continue

    def record(self, name: str, when: datetime | None = None) -> None:
        """Count one call. Flushes at most once a minute — a chatty turn can fire several tools,
        and a write per call would put the disk in the latency path of every tool the owner uses."""
        if not name:
            return
        with self._lock:
            self._counts[name] = self._counts.get(name, 0) + 1
            self._last[name] = (when or datetime.now()).isoformat(timespec="seconds")
            self._dirty = True
            due = (time.monotonic() - self._last_flush) >= _FLUSH_EVERY_S
        if due:
            self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            payload = {
                "updated": datetime.now().isoformat(timespec="seconds"),
                "tools": {n: {"calls": c, "last": self._last.get(n)}
                          for n, c in sorted(self._counts.items(), key=lambda kv: -kv[1])},
            }
            self._dirty = False
            self._last_flush = time.monotonic()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self._path)   # atomic: a killed process leaves the old file, never a torn one
        except Exception as e:  # noqa: BLE001
            logger.warning(f"tool usage flush failed: {e}")

    def counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    def top(self, n: int = 10) -> list[tuple[str, int]]:
        """The n most-called tools, most-used first."""
        with self._lock:
            return sorted(self._counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]

    def unused(self, known: list[str]) -> list[str]:
        """Names in `known` this store has never seen called. The point of the whole file: a tool
        that has never been called is a tiering candidate — but ONLY once the store has run long
        enough to be evidence, which `total()` is there to let a caller check."""
        with self._lock:
            return sorted(n for n in known if n not in self._counts)

    def stale(self, known: list[str], days: int = 90) -> list[tuple[str, str]]:
        """03.R4 — `(name, last_called_or_"never")` for every tool not fired inside `days`.

        `unused()` answers "never called", which on a young store is almost everything. This answers
        the question the sweep actually asks: what has gone quiet. A tool with no timestamp is
        reported as "never" rather than omitted, because the two are the same decision — nothing has
        used it — and dropping one of them would make the report look shorter than the truth.
        """
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(days=max(0, days))
        out: list[tuple[str, str]] = []
        with self._lock:
            for name in known:
                last = self._last.get(name)
                if not last:
                    out.append((name, "never"))
                    continue
                try:
                    if datetime.fromisoformat(last) < cutoff:
                        out.append((name, last))
                except ValueError:
                    out.append((name, "never"))   # unreadable stamp is not evidence of use
        return sorted(out)

    def total(self) -> int:
        with self._lock:
            return sum(self._counts.values())


USAGE = ToolUsage()
