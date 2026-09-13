"""L5 semantic recall — find facts by *meaning*, not just keywords. Strictly optional.

Keyword recall (``MemoryStore.recall``) misses "what do you know about my bunnies?" when the stored
fact says "rabbit farm". This layer closes that gap by ranking learned facts on embedding cosine
similarity and blending that into the keyword score.

It is **entirely graceful**: if no local embedder is installed it reports ``available == False`` and
recall silently stays keyword-only — no crash, no slow model download forced on anyone. When
``sentence-transformers`` *is* present, a small CPU model (``all-MiniLM-L6-v2`` by default) is loaded
lazily on first use. Per-fact embeddings are memoised by (path, mtime) so re-ranking is cheap and a
fact re-embeds only when its file changes.

The embedder is injectable (``SemanticIndex(embed_fn=...)``) so it can be unit-tested with a tiny
deterministic stub — no heavy dependency needed to verify the ranking maths.
"""

from __future__ import annotations

import math
import sqlite3
import struct
import threading
from pathlib import Path
from typing import Callable, Sequence

from loguru import logger

from afon.brain.dbconn import connect
from afon.config import settings
from afon.shared.paths import store_path

Vector = Sequence[float]
EmbedFn = Callable[[list[str]], list[Vector]]



def _vector_db_path() -> Path:
    return Path(settings.memory_vector_db_path) if settings.memory_vector_db_path \
        else store_path("vectors")


class VectorStore:
    """Persistent (key, mtime) -> embedding cache in sqlite so restarts don't re-embed everything.

    ponytail: dead simple. One table, key is the primary key, vector stored as packed float32 bytes.
    A row is only reused when its mtime matches (a changed fact re-embeds). All failures degrade to
    "not cached" — a broken DB never blocks recall, it just means we recompute. Thread-safe via a
    lock + per-call connection (the HTTP thread and agent thread both touch it).
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _vector_db_path()
        self._lock = threading.Lock()
        self._ready = False

    def _conn(self) -> sqlite3.Connection:
        c = connect(self._path, rows=False)
        if not self._ready:
            c.execute("CREATE TABLE IF NOT EXISTS embeddings "
                      "(key TEXT PRIMARY KEY, mtime REAL NOT NULL, vec BLOB NOT NULL)")
            c.commit()
            self._ready = True
        return c

    def get_many(self, keys_mtimes: list[tuple[str, float]]) -> dict[str, Vector]:
        """Return {key: vector} for rows whose stored mtime matches the requested mtime."""
        if not keys_mtimes:
            return {}
        want = {k: m for k, m in keys_mtimes}
        out: dict[str, Vector] = {}
        try:
            with self._lock, self._conn() as c:
                qs = ",".join("?" * len(want))
                rows = c.execute(
                    f"SELECT key, mtime, vec FROM embeddings WHERE key IN ({qs})",
                    list(want.keys()),
                ).fetchall()
            for key, mtime, blob in rows:
                if abs(mtime - want[key]) < 1e-6:
                    out[key] = list(struct.unpack(f"{len(blob) // 4}f", blob))
        except sqlite3.Error as e:  # noqa: BLE001
            logger.debug(f"vector store read failed ({type(e).__name__}); recomputing")
        return out

    def forget(self, keys: list[str]) -> int:
        """Drop cached embeddings by key. Returns how many rows went (37.F3).

        A deleted fact's vector was harmless to recall — nothing queries a note that is gone — and
        was still the fact, on disk, in a store the owner had been told no longer held it. "Forget"
        that leaves the embedding behind is the failure 37.F3 names.
        """
        if not keys:
            return 0
        try:
            with self._lock, self._conn() as c:
                qs = ",".join("?" * len(keys))
                cur = c.execute(f"DELETE FROM embeddings WHERE key IN ({qs})", list(keys))
                c.commit()
                return int(cur.rowcount or 0)
        except sqlite3.Error as e:  # noqa: BLE001
            logger.warning(f"vector store delete failed ({type(e).__name__}: {e})")
            return 0

    def keys(self) -> list[str]:
        """Every cached key, so a caller can find the rows whose note no longer exists."""
        try:
            with self._lock, self._conn() as c:
                return [r[0] for r in c.execute("SELECT key FROM embeddings").fetchall()]
        except sqlite3.Error as e:  # noqa: BLE001
            logger.debug(f"vector store key scan failed ({type(e).__name__})")
            return []

    def put_many(self, rows: list[tuple[str, float, Vector]]) -> None:
        if not rows:
            return
        try:
            packed = [(k, m, struct.pack(f"{len(v)}f", *v)) for k, m, v in rows]
            with self._lock, self._conn() as c:
                c.executemany(
                    "INSERT INTO embeddings(key, mtime, vec) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET mtime=excluded.mtime, vec=excluded.vec",
                    packed,
                )
                c.commit()
        except sqlite3.Error as e:  # noqa: BLE001
            logger.debug(f"vector store write failed ({type(e).__name__}); staying in-memory only")


def _cosine(a: Vector, b: Vector) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class SemanticIndex:
    """Lazy, optional embedding ranker. ``available`` is False until an embedder loads."""

    def __init__(self, embed_fn: EmbedFn | None = None, model_name: str | None = None,
                 store: "VectorStore | None" = None, persist: bool = True) -> None:
        self._embed_fn = embed_fn          # injected (tests) — skips model loading entirely
        self._model = None                 # lazily-loaded sentence-transformers model
        self._model_name = model_name or settings.memory_semantic_model
        self._load_failed = False
        self._cache: dict[tuple[str, float], Vector] = {}  # (key, mtime) -> embedding (hot, in-proc)
        # Persistent L5 cache: survives restarts so we don't re-embed (or re-hit the Jina API) every
        # boot. Tests pass persist=False (pure in-memory) or inject their own store.
        self._store = store if store is not None else (VectorStore() if persist else None)

    # ---- embedder plumbing ------------------------------------------------------------
    def _ensure_embedder(self) -> EmbedFn | None:
        if self._embed_fn is not None:
            return self._embed_fn
        if self._model is not None:
            return self._model_embed
        if self._load_failed:
            return None
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self._model = SentenceTransformer(self._model_name)
            logger.info(f"L5 semantic recall: loaded embedder '{self._model_name}'")
            return self._model_embed
        except Exception as e:  # noqa: BLE001
            # No local model (~1GB torch) — fall back to the Jina embeddings API if its key is
            # already configured (it powers the web reader too). No key = honest keyword-only.
            if settings.jina_api_key:
                logger.info("L5 semantic recall: using Jina embeddings API (no local model)")
                return self._jina_embed
            self._load_failed = True
            logger.info(f"L5 semantic recall unavailable ({type(e).__name__}); keyword recall only")
            return None

    def _model_embed(self, texts: list[str]) -> list[Vector]:
        return [list(v) for v in self._model.encode(texts, normalize_embeddings=False)]

    def _jina_embed(self, texts: list[str]) -> list[Vector]:
        # The one synchronous outbound call in the brain: the embedder is called from sync recall
        # paths, and making it async would push a rewrite through every caller for no behavioural
        # gain. It still reads its timeout from the one policy table (20.F3) rather than carrying
        # a number of its own.
        import httpx

        from afon.brain.tools.base import policy_for

        url = "https://api.jina.ai/v1/embeddings"
        r = httpx.post(
            url,
            headers={"Authorization": f"Bearer {settings.jina_api_key}"},
            json={"model": "jina-embeddings-v3", "task": "text-matching", "input": texts},
            timeout=policy_for(url).timeout,
        )
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d["index"])
        return [d["embedding"] for d in data]

    @property
    def available(self) -> bool:
        return self._ensure_embedder() is not None

    # ---- ranking ----------------------------------------------------------------------
    def _embed_cached(self, embed: EmbedFn, items: list[tuple[str, float, str]]) -> dict[str, Vector]:
        """Embed each (cache_key, mtime, text), reusing cached vectors when unchanged."""
        out: dict[str, Vector] = {}
        to_compute: list[tuple[str, str]] = []  # (cache_key, text)
        mtime_by_key = {k: m for k, m, _ in items}
        for key, mtime, text in items:
            cached = self._cache.get((key, mtime))
            if cached is not None:
                out[key] = cached
            else:
                to_compute.append((key, text))
        # Second chance: the persistent store may have a vector from a prior run (before restart).
        if to_compute and self._store is not None:
            hits = self._store.get_many([(k, mtime_by_key[k]) for k, _ in to_compute])
            for key, vec in hits.items():
                out[key] = vec
                self._cache[(key, mtime_by_key[key])] = vec  # warm the hot cache
            to_compute = [(k, t) for k, t in to_compute if k not in hits]
        if to_compute:
            vecs = embed([t for _, t in to_compute])
            fresh: list[tuple[str, float, Vector]] = []
            for (key, _), vec in zip(to_compute, vecs):
                out[key] = vec
                self._cache[(key, mtime_by_key[key])] = vec
                fresh.append((key, mtime_by_key[key], vec))
            if self._store is not None:
                self._store.put_many(fresh)  # persist so the next boot skips this work
        return out

    def scores(self, query: str, items: list[tuple[str, float, str]]) -> dict[str, float]:
        """Cosine similarity in [0,1]-ish per cache_key, or {} if no embedder is available.

        ``items`` are (cache_key, mtime, text). The cache_key identifies a fact for memoisation.
        """
        embed = self._ensure_embedder()
        if embed is None or not items:
            return {}
        try:
            qvec = embed([query])[0]
            vecs = self._embed_cached(embed, items)
            return {key: _cosine(qvec, vecs[key]) for key in vecs}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"semantic scoring failed ({type(e).__name__}); keyword-only this call")
            return {}


# Process-wide index (keyword-only until/unless an embedder is installed).
INDEX = SemanticIndex()
