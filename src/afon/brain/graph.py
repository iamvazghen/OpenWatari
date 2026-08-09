"""L5b graph memory — associative recall over an entity-relation graph (TODO 8.6), the LEAN way.

The roadmap floated Neo4j here. For a single-user assistant that's absurd — a 2 GB JVM heap to store
a few hundred relations. Neo4j earns its keep at millions of edges with real graph algorithms; we have
neither. So this is the honest lean version: a no-server sqlite triple store (subject, predicate,
object) with 1–2 hop neighbour traversal. It gives the one thing flat Markdown memory can't — *multi-
hop* recall ("my rabbit farm's country" → farm →located_in→ Armenia) — at zero infra cost.

Deliberate simplifications (ponytail — marked, not hidden):
  * No automatic entity/relation extraction (NER is the expensive, brittle part). Relations are added
    explicitly via the ``link_memory`` tool or the self-improvement loop. When nothing's linked, graph
    recall is simply empty and the other memory layers carry the turn.
  * Traversal is a plain breadth-first hop over an in-DB index, capped at 2 hops and a small fan-out.
    No weights, no shortest-path, no PageRank — none of which a voice turn needs.
  * Entity match is case-insensitive exact/substring, not embedding-fuzzy. The L5 vector layer already
    covers fuzzy; the graph is for *structured* links you asserted on purpose.
"""

from __future__ import annotations

import sqlite3
import threading
from collections import deque
from pathlib import Path

from loguru import logger

from afon.config import settings

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _graph_db_path() -> Path:
    return Path(settings.memory_graph_db_path) if settings.memory_graph_db_path \
        else _REPO_ROOT / "afon_graph.sqlite"


def _norm(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


class GraphMemory:
    """A tiny persistent triple store. Thread-safe, degrades to no-op on any DB error."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _graph_db_path()
        self._lock = threading.Lock()
        self._ready = False

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self._path, timeout=5)
        if not self._ready:
            c.execute(
                "CREATE TABLE IF NOT EXISTS triples ("
                "subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT NOT NULL, "
                "PRIMARY KEY(subject, predicate, object))"
            )
            c.execute("CREATE INDEX IF NOT EXISTS ix_subject ON triples(subject)")
            c.execute("CREATE INDEX IF NOT EXISTS ix_object ON triples(object)")
            c.commit()
            self._ready = True
        return c

    def add(self, subject: str, predicate: str, obj: str) -> bool:
        s, p, o = _norm(subject), _norm(predicate), _norm(obj)
        if not (s and p and o):
            return False
        try:
            with self._lock, self._conn() as c:
                c.execute("INSERT OR IGNORE INTO triples(subject, predicate, object) VALUES(?,?,?)",
                          (s, p, o))
                c.commit()
            return True
        except sqlite3.Error as e:  # noqa: BLE001
            logger.debug(f"graph add failed ({type(e).__name__}); skipping")
            return False

    def remove(self, subject: str, predicate: str | None = None, obj: str | None = None) -> int:
        """Delete triples matching subject (and optional predicate/object). Returns rows removed."""
        s = _norm(subject)
        if not s:
            return 0
        clauses, params = ["(subject = ? OR object = ?)"], [s, s]
        if predicate:
            clauses.append("predicate = ?")
            params.append(_norm(predicate))
        if obj:
            clauses.append("(subject = ? OR object = ?)")
            params.extend([_norm(obj), _norm(obj)])
        try:
            with self._lock, self._conn() as c:
                cur = c.execute(f"DELETE FROM triples WHERE {' AND '.join(clauses)}", params)
                c.commit()
                return cur.rowcount
        except sqlite3.Error as e:  # noqa: BLE001
            logger.debug(f"graph remove failed ({type(e).__name__})")
            return 0

    def neighbors(self, entity: str) -> list[tuple[str, str, str]]:
        """All triples in which ``entity`` is the subject or the object (one hop)."""
        e = _norm(entity)
        if not e:
            return []
        try:
            with self._lock, self._conn() as c:
                rows = c.execute(
                    "SELECT subject, predicate, object FROM triples WHERE subject = ? OR object = ?",
                    (e, e),
                ).fetchall()
            return [tuple(r) for r in rows]
        except sqlite3.Error as e2:  # noqa: BLE001
            logger.debug(f"graph neighbors failed ({type(e2).__name__})")
            return []

    def related(self, entity: str, hops: int = 2, fan_out: int = 25) -> list[str]:
        """Entities reachable from ``entity`` within ``hops`` (BFS, excludes the seed itself)."""
        seed = _norm(entity)
        if not seed:
            return []
        seen = {seed}
        order: list[str] = []
        frontier: deque[tuple[str, int]] = deque([(seed, 0)])
        while frontier and len(order) < fan_out:
            node, depth = frontier.popleft()
            if depth >= hops:
                continue
            for s, _p, o in self.neighbors(node):
                for other in (s, o):
                    if other not in seen:
                        seen.add(other)
                        order.append(other)
                        frontier.append((other, depth + 1))
        return order[:fan_out]

    def describe(self, entity: str) -> list[str]:
        """Human-readable one-line facts for an entity's direct links ('X predicate Y')."""
        e = _norm(entity)
        out = []
        for s, p, o in self.neighbors(e):
            out.append(f"{s} {p} {o}")
        return out

    def all_triples(self) -> list[tuple[str, str, str]]:
        try:
            with self._lock, self._conn() as c:
                return [tuple(r) for r in c.execute(
                    "SELECT subject, predicate, object FROM triples").fetchall()]
        except sqlite3.Error:  # noqa: BLE001
            return []


# Process-wide singleton.
GRAPH = GraphMemory()
