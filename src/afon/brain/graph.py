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

from afon.brain.dbconn import connect
from afon.shared.entities import canonical
from afon.config import settings

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _graph_db_path() -> Path:
    return Path(settings.memory_graph_db_path) if settings.memory_graph_db_path \
        else _REPO_ROOT / "afon_graph.sqlite"


def _norm(s: str) -> str:
    """The key a node is stored under (24.F1).

    Was lowercase + whitespace collapse, which left a name's Latin spelling, its short form and
    its Cyrillic original as three separate people holding a third of the facts each. `canonical`
    folds transliteration, accents and titles as well, so one human is one node. Rows written under the old spelling are
    rewritten once, on first open — see `_migrate_canonical`.
    """
    return canonical(s)


class GraphMemory:
    """A tiny persistent triple store. Thread-safe, degrades to no-op on any DB error."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _graph_db_path()
        self._lock = threading.Lock()
        self._ready = False

    def _conn(self) -> sqlite3.Connection:
        c = connect(self._path, rows=False)
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
            self._migrate_canonical(c)
        return c

    def _migrate_canonical(self, c: sqlite3.Connection) -> None:
        """Rewrite rows stored under the pre-24.F1 key so existing facts stay reachable.

        Without this the normalisation change ORPHANS every accented or Cyrillic node: the data is
        still there and no lookup can ever reach it again, which is worse than not changing the
        key at all. Idempotent — a second run finds nothing to do. INSERT OR IGNORE because two old
        spellings can collapse onto one canonical row, which is the entire point.
        """
        try:
            rows = c.execute("SELECT subject, predicate, object FROM triples").fetchall()
            moved = 0
            for subj, pred, obj in rows:
                new = (_norm(subj), _norm(pred), _norm(obj))
                if new == (subj, pred, obj):
                    continue
                c.execute("INSERT OR IGNORE INTO triples VALUES (?,?,?)", new)
                c.execute("DELETE FROM triples WHERE subject=? AND predicate=? AND object=?",
                          (subj, pred, obj))
                moved += 1
            if moved:
                c.commit()
                logger.info(f"graph: re-keyed {moved} triple(s) onto canonical entity names (24.F1)")
        except sqlite3.Error as e:  # noqa: BLE001 — a failed migration must not break the store
            logger.warning(f"graph: canonical re-key skipped ({e})")

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
        e = self.resolve(entity)
        out = []
        for s, p, o in self.neighbors(e):
            out.append(f"{s} {p} {o}")
        return out

    def resolve(self, entity: str) -> str:
        """The stored key for this entity, merging spelling variants at LOOKUP time (24.F1).

        `_norm` is deterministic and does not guess: two Latin spellings of the same name can
        differ by a letter ("gh" vs "g") that no rule can settle, so they canonicalise apart.
        Merging them by rewriting one into the other would bake a guess into storage permanently. Doing it
        here instead costs a scan of the subject list and is wrong in the cheap direction — a
        missed match, not a mis-keyed node.
        """
        key = _norm(entity)
        if not key:
            return key
        try:
            with self._lock, self._conn() as c:
                exists = c.execute("SELECT 1 FROM triples WHERE subject=? LIMIT 1",
                                   (key,)).fetchone()
                if exists:
                    return key
                subjects = [r[0] for r in c.execute(
                    "SELECT DISTINCT subject FROM triples").fetchall()]
        except sqlite3.Error:  # noqa: BLE001
            return key
        from afon.shared.entities import best_match
        return best_match(key, subjects) or key

    def predicates_of(self, entity: str) -> list[str]:
        """Which relations are recorded ABOUT this entity (as the subject)."""
        e = self.resolve(entity)
        return sorted({p for s, p, _o in self.neighbors(e) if s == e})

    def gaps(self, entity: str, kind: str = "person") -> list[str]:
        """What is NOT known about this entity, from the declared facets (24.F2).

        Read from a DECLARED list rather than from what happens to be stored, because a gap you
        can only notice by already knowing what to look for is a gap nobody notices. This is the
        same reason the loop registry (31.F4) is declared rather than collected.
        """
        from afon.shared.entities import unknowns
        return unknowns(self.predicates_of(entity), kind)

    def all_triples(self) -> list[tuple[str, str, str]]:
        try:
            with self._lock, self._conn() as c:
                return [tuple(r) for r in c.execute(
                    "SELECT subject, predicate, object FROM triples").fetchall()]
        except sqlite3.Error:  # noqa: BLE001
            return []


# Process-wide singleton.
GRAPH = GraphMemory()
