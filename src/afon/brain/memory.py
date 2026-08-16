"""Afon's persistent memory — the learned (L1) + journal (L2) layers.

The static `memory/*.md` files are who the owner *is*; this is what Afon *learns* as they talk:
one fact per note under `memory/learned/`, and a per-day journal under `memory/journal/` for
continuity ("what did we do yesterday?"). Plain Markdown so it stays the source of truth, editable
by hand, and later indexable by the Redis (L4) / vector (L5) accelerators without changing it.

Recall is a lightweight keyword + recency scorer — good enough for "what do you know about the
rabbit farm?" voice queries, and fast (no model, no network). Everything degrades gracefully: a
missing dir is created on first write; reading an absent store just returns nothing.
"""

from __future__ import annotations

import asyncio

import os
import re
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from afon.config import settings

# repo root = .../src/afon/brain/memory.py -> parents[3]
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MEMORY_DIR = _REPO_ROOT / "memory"

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def _slug(text: str, max_words: int = 6) -> str:
    words = _SLUG_RE.sub("-", text.lower()).strip("-").split("-")
    return "-".join(w for w in words if w)[:60] or "note"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


# Function words carry no topical signal but match almost every stored fact ("the owner…"), which
# floods keyword recall with false hits — harmless for a deliberate `recall` query, but noise when
# auto-recall runs every turn. Dropping them makes a term match mean something. Kept small + generic.
_STOPWORDS = frozenset((
    "the", "a", "an", "of", "to", "in", "on", "at", "for", "and", "or", "but", "is", "are", "was",
    "were", "be", "been", "being", "am", "do", "does", "did", "have", "has", "had", "it", "its",
    "this", "that", "these", "those", "my", "your", "his", "her", "our", "their", "me", "you", "i",
    "we", "he", "she", "they", "them", "so", "as", "if", "then", "than", "with", "about", "into",
    "what", "whats", "which", "who", "whom", "how", "when", "where", "why", "can", "could", "would",
    "should", "will", "shall", "now", "right", "just", "please", "tell", "give", "get", "some", "any",
    "there", "here", "up", "out", "by", "from", "not", "no", "yes", "ok", "okay", "again", "still",
))


def _terms(query: str) -> list[str]:
    """Meaningful search terms from a query — >1 char and not a function word (stopword)."""
    raw = [t for t in _SLUG_RE.sub(" ", query.lower()).split() if len(t) > 1]
    filtered = [t for t in raw if t not in _STOPWORDS]
    # If a query is *all* stopwords ("what is it"), fall back to the raw terms so recall isn't blank.
    return filtered or raw


# ── provenance (SYSTEMS.md 30.F3) ────────────────────────────────────────────────────────────
# "Every stored fact carries source, timestamp and confidence." Until now a fact carried its text,
# its tags and a `created` line, and nothing recorded WHERE it came from. That matters because the
# store mixes three very different kinds of claim: things the owner said outright, things the
# background reviewer inferred from a conversation, and things the pattern detector guessed from
# behaviour. They were indistinguishable once written, so a guess and a statement were recalled
# with equal authority — which is how a wrong inference becomes something Afon "knows".
#
# The vocabulary is closed. An open string would drift into a dozen spellings of the same origin
# and nothing downstream (30.R2's "I have two versions", 30.R4's confidence floor) could group on
# it. `legacy` names the notes written before this existed: their provenance is genuinely unknown,
# and saying so is more honest than backfilling a confidence nobody measured.
SOURCES: dict[str, float] = {
    "owner": 1.0,        # he said it. Nothing outranks this.
    "tool": 0.9,         # read out of a system of record (calendar, tasks, vault)
    "inferred": 0.6,     # the background reviewer read it out of a conversation
    "pattern": 0.5,      # the detector noticed it in behaviour; a guess with evidence
    "legacy": 0.5,       # written before provenance existed — unknown, and named as such
}
DEFAULT_SOURCE = "inferred"


def source_rank(source: str) -> float:
    return SOURCES.get(source, 0.0)


def _iso(mtime: float) -> str:
    return datetime.fromtimestamp(mtime, timezone.utc).isoformat()


class LearnedNote:
    __slots__ = ("path", "text", "tags", "created", "mtime", "source", "confidence")

    def __init__(self, path: Path, text: str, tags: list[str], created: str,
                 mtime: float = 0.0, source: str = "legacy",
                 confidence: float | None = None) -> None:
        self.path = path
        self.text = text
        self.tags = tags
        self.created = created
        self.mtime = mtime   # cached at read time so hot-path callers needn't re-stat
        self.source = source if source in SOURCES else "legacy"
        self.confidence = SOURCES[self.source] if confidence is None else confidence


class MemoryStore:
    """Filesystem-backed learned facts (L1) + daily journal (L2)."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self.base = Path(base_dir) if base_dir else DEFAULT_MEMORY_DIR
        self.learned_dir = self.base / "learned"
        self.journal_dir = self.base / "journal"
        # Parse cache: path -> (mtime, LearnedNote). recall()/digest() run on the hot path and were
        # re-reading + re-parsing every fact file on every call (~17ms for the corpus). We now read +
        # parse a file only when it's new or its mtime changed; an unchanged corpus is served from
        # memory (sub-millisecond). External edits/deletes are still picked up via the mtime/glob scan.
        self._note_cache: dict[str, tuple[float, LearnedNote]] = {}

    # ---- L1: learned facts ------------------------------------------------------------
    def remember(self, text: str, tags: list[str] | None = None, *,
                 source: str = DEFAULT_SOURCE, confidence: float | None = None) -> Path | None:
        """Store one learned fact with its provenance (30.F3).

        `source` must be one of `SOURCES`; an unknown one is stored as the default rather than
        inventing a category, because a vocabulary nothing enforces is a vocabulary that drifts.
        """
        text = (text or "").strip()
        if not text:
            return None
        tags = [t.strip().lower() for t in (tags or []) if t.strip()]
        if source not in SOURCES:
            logger.warning(f"memory: unknown source {source!r} — storing as {DEFAULT_SOURCE}")
            source = DEFAULT_SOURCE
        conf = SOURCES[source] if confidence is None else max(0.0, min(1.0, float(confidence)))
        # Dedup: identical normalised fact already stored -> return it, don't duplicate.
        for note in self._iter_notes():
            if _norm(note.text) == _norm(text):
                # ...but a better source is new information. The owner stating outright something
                # Afon had merely inferred is exactly the moment a guess becomes a fact, and
                # silently keeping the weaker provenance would throw that away.
                if source_rank(source) > source_rank(note.source):
                    self._rewrite_provenance(note, source, conf)
                    logger.info(f"memory: promoted {text[:40]!r} from {note.source} to {source}")
                else:
                    logger.info(f"memory: already knew {text[:50]!r}")
                return note.path
        self.learned_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc)
        fname = f"{ts:%Y%m%d-%H%M%S}-{_slug(text)}.md"
        path = self.learned_dir / fname
        tagline = ", ".join(tags)
        path.write_text(
            f"---\ncreated: {ts.isoformat()}\ntags: {tagline}\n"
            f"source: {source}\nconfidence: {conf:.2f}\n---\n{text}\n",
            encoding="utf-8",
        )
        logger.info(f"memory: learned {text[:60]!r} [{source} {conf:.2f}]"
                    + (f" [{tagline}]" if tags else ""))
        return path

    def _rewrite_provenance(self, note: LearnedNote, source: str, confidence: float) -> None:
        """Upgrade a stored fact's provenance in place, keeping its text and original timestamp."""
        try:
            raw = note.path.read_text(encoding="utf-8", errors="ignore")
            m = _FRONTMATTER_RE.match(raw)
            body = m.group(2).strip() if m else raw.strip()
            keep = [ln for ln in (m.group(1).splitlines() if m else [])
                    if not ln.startswith(("source:", "confidence:"))]
            if not any(ln.startswith("created:") for ln in keep):
                keep.insert(0, f"created: {note.created or _iso(note.mtime)}")
            meta = "\n".join(keep + [f"source: {source}", f"confidence: {confidence:.2f}"])
            note.path.write_text(f"---\n{meta}\n---\n{body}\n", encoding="utf-8")
            self._note_cache.pop(str(note.path), None)   # force a re-parse on the next read
        except OSError as e:
            logger.debug(f"memory: could not upgrade provenance of {note.path.name}: {e}")

    def _parse_note(self, p: Path, raw: str) -> LearnedNote:
        m = _FRONTMATTER_RE.match(raw)
        source, confidence = "legacy", None
        if m:
            meta, body = m.group(1), m.group(2).strip()
            tags: list[str] = []
            created = ""
            for line in meta.splitlines():
                if line.startswith("tags:"):
                    tags = [t.strip().lower() for t in line[5:].split(",") if t.strip()]
                elif line.startswith("created:"):
                    created = line[8:].strip()
                elif line.startswith("source:"):
                    source = line[7:].strip().lower()
                elif line.startswith("confidence:"):
                    try:
                        confidence = max(0.0, min(1.0, float(line[11:].strip())))
                    except ValueError:
                        confidence = None
        else:
            body, tags, created = raw.strip(), [], ""
        return LearnedNote(p, body, tags, created, source=source, confidence=confidence)

    def _iter_notes(self):
        if not self.learned_dir.is_dir():
            return
        seen: set[str] = set()
        # os.scandir hands back DirEntry objects whose .stat() is cached from the single directory
        # read (especially cheap on Windows, where a separate Path.stat() is a full extra syscall) —
        # so the freshness check costs ~one syscall for the whole directory, not one per file.
        try:
            entries = list(os.scandir(self.learned_dir))
        except OSError:
            return
        for entry in entries:
            if not entry.name.endswith(".md"):
                continue
            key = entry.path
            seen.add(key)
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            cached = self._note_cache.get(key)
            if cached and cached[0] == mtime:
                yield cached[1]
                continue
            try:
                raw = Path(entry.path).read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            note = self._parse_note(Path(entry.path), raw)
            note.mtime = mtime
            if not note.created:
                # 30.F3 wants a timestamp on every fact, and a note written without frontmatter
                # has none. The file's own mtime is the only honest answer available, and it is
                # better than the empty string `salient_notes` was already having to guess around.
                note.created = _iso(mtime)
            self._note_cache[key] = (mtime, note)
            yield note
        # Forget files that have since been deleted, so the cache can't grow unbounded or serve ghosts.
        if len(self._note_cache) > len(seen):
            for stale in [k for k in self._note_cache if k not in seen]:
                self._note_cache.pop(stale, None)

    def recall(self, query: str, limit: int = 5, semantic=None) -> list[str]:
        """Best matching learned facts for ``query``.

        Always runs the fast keyword scorer (count + tag boost). When an L5 semantic index is
        available (an embedder is installed), it *also* blends in cosine similarity so facts that
        match by meaning — "bunnies" → "rabbit farm" — surface even with zero literal overlap.
        Pass ``semantic=False`` to force keyword-only; ``semantic`` may be an injected index (tests).
        """
        query = (query or "").strip()
        if not query:
            return []
        terms = _terms(query)

        notes = list(self._iter_notes())
        # Keyword score per note (by file path, used as a stable key).
        kw: dict[str, float] = {}
        text_by_key: dict[str, str] = {}
        mtime_by_key: dict[str, float] = {}
        for note in notes:
            key = str(note.path)
            text_by_key[key] = note.text
            mtime_by_key[key] = note.mtime   # cached at read time (no re-stat on the hot path)
            low = note.text.lower()
            score = 0.0
            for t in terms:
                score += low.count(t)
                if t in note.tags:
                    score += 5
            kw[key] = score

        # Optional semantic blend.
        sem: dict[str, float] = {}
        use_sem = semantic if semantic is not None else settings.memory_semantic_enabled
        if use_sem:
            index = semantic if hasattr(semantic, "scores") else None
            if index is None:
                from afon.brain.semantic import INDEX as index  # type: ignore
            items = [(k, mtime_by_key[k], text_by_key[k]) for k in text_by_key]
            sem = index.scores(query, items)

        weight = settings.memory_semantic_weight
        floor = settings.memory_semantic_min_score
        combined: list[tuple[float, float, str]] = []
        for key in text_by_key:
            # Only count semantic similarity above the floor — otherwise near-zero cosines let every
            # fact leak into results for an unrelated query (keyword hits are always kept).
            s = sem.get(key, 0.0)
            total = kw.get(key, 0.0) + (weight * s if s >= floor else 0.0)
            if total > 0:
                combined.append((total, mtime_by_key[key], text_by_key[key]))
        combined.sort(key=lambda s: (s[0], s[1]), reverse=True)
        return [text for _, _, text in combined[:limit]]

    async def fused_recall(self, query: str, limit: int = 8, layers: tuple[str, ...] | None = None) -> list[dict]:
        """Cross-layer recall — returns ranked hits with layer tags so the LLM can cite them.

        Default layers: ("L1", "L2", "L3", "L5", "L5b"). Each hit is a dict::

            {"layer": "L1", "text": "...", "score": 4.0, "source": "memory/learned/..."}

        L1 = learned facts (with optional L5 semantic blend).
        L2 = journal entries (most recent days only — journal grows fast; cap scan).
        L3 = vault notes (Obsidian) via search_vault tool.
        L5 = re-ranks L1 hits by cosine similarity when available (no separate layer; folds into L1).
        L5b = the entity graph (772 triples in production) — the store J3.3 called "no unified
              recall" and the only one that was genuinely unreachable from here.

        Falls back gracefully: any unavailable layer is silently skipped (no error, no crash).
        """
        query = (query or "").strip()
        if not query:
            return []
        layers = layers or ("L1", "L2", "L3", "L5", "L5b")
        hits: list[dict] = []

        # L1 (+ L5 semantic re-rank of L1)
        #
        # OFFLOADED TO A THREAD. `recall()` is synchronous, and with L5 active it makes a
        # BLOCKING httpx.post to api.jina.ai with timeout=10. Awaited directly from this async
        # method it held the event loop — measured at 1.9s on a warm cache — during which the
        # brain answers nothing at all: other turns, the WebSocket and the scheduler are frozen
        # together. The embedder's speed was never the problem; running it on the loop was.
        if "L1" in layers:
            try:
                texts = await asyncio.to_thread(
                    self.recall, query, limit, ("L5" in layers))
                for i, t in enumerate(texts):
                    # `recall` is already sorted, so the first hit is the best.
                    score = max(0.0, float(limit - i))
                    hits.append({"layer": "L1", "text": t, "score": score, "source": "learned"})
            except Exception as e:  # noqa: BLE001
                logger.debug(f"fused_recall: L1 layer skipped ({e})")

        # L2 journal — scan the last 14 days' entries (each entry is one bullet).
        # Also threaded: it is only ~6ms today, but it is unbounded synchronous file I/O on a
        # directory that grows daily, and it sits on the same loop as everything else.
        if "L2" in layers:
            try:
                terms = _terms(query)
                if terms and self.journal_dir.is_dir():
                    await asyncio.sleep(0)   # yield before a file scan
                    days = sorted(self.journal_dir.glob("*.md"), reverse=True)[:14]
                    for day in days:
                        try:
                            raw = day.read_text(encoding="utf-8", errors="ignore")
                        except OSError:
                            continue
                        # Entries are "- **HH:MM** — summary" — split on the dash lines.
                        for line in raw.splitlines():
                            if not line.startswith("- "):
                                continue
                            low = line.lower()
                            score = sum(low.count(t) for t in terms)
                            if score > 0:
                                hits.append({"layer": "L2", "text": line.lstrip("- ").strip(),
                                             "score": float(score), "source": str(day.name)})
            except Exception as e:  # noqa: BLE001
                logger.debug(f"fused_recall: L2 layer skipped ({e})")

        # L3 vault — delegate to the search_vault tool when available.
        if "L3" in layers:
            try:
                from afon.brain.tools.vault import search_vault
                # Lazy import: `clip` lives in tools.base.py, which is in the same package as the
                # vault tools being imported above. Eagerly importing tools.base at the top of this
                # file would create a circular import (memory -> tools -> tools.memory -> memory).
                from afon.brain.tools.base import clip as _clip  # noqa: PLC0415
                # BOUNDED, and the budget now works. `search_vault` used to be `async def`
                # around a purely synchronous body, so it never yielded — and wait_for cannot
                # interrupt a coroutine that never yields (measured: 7.5s of dead loop against a
                # 6s timeout). This used to compensate with a to_thread(asyncio.run(...)) sandwich
                # right here; the thread offload now lives inside search_vault, where it covers
                # the LLM calling that tool directly too. The budget caps how long recall WAITS.
                res = await asyncio.wait_for(
                    search_vault({"query": query, "limit": limit}),
                    timeout=settings.memory_vault_search_budget_s)
                if res and "couldn't" not in res.lower() and "no " not in res.lower()[:30]:
                    # search_vault returns a multi-line list; split into bullets for ranking.
                    for line in res.splitlines():
                        line = line.strip()
                        if not line or line.lower().startswith(("here", "nothing", "no ", "i ")):
                            continue
                        # Tiny length-weighted score so longer hits don't dominate the per-layer rank.
                        hits.append({"layer": "L3", "text": _clip(line, 240),
                                     "score": 2.0 + min(2.0, len(line) / 200.0),
                                     "source": "vault"})
            except asyncio.TimeoutError:
                logger.warning(
                    f"fused_recall: L3 vault search exceeded "
                    f"{settings.memory_vault_search_budget_s}s — returning without it")
            except Exception as e:  # noqa: BLE001
                logger.debug(f"fused_recall: L3 layer skipped ({e})")

        # L5b graph — the store J3.3 is actually about. It is ENTITY-KEYED (neighbors/describe,
        # no free-text search), so the join to a natural-language query is `_terms()` — the same
        # tokeniser L2 uses. ~11ms for four terms, and deliberately NOT in the per-turn
        # ("L1","L2") path: that path is ~10ms and is the reason turns stay fast.
        if "L5b" in layers:
            try:
                from afon.brain.graph import GRAPH  # noqa: PLC0415

                def _graph_hits() -> list[str]:
                    lines, seen = [], set()
                    for term in _terms(query)[:6]:
                        for line in GRAPH.describe(term)[:4]:
                            if line not in seen:
                                seen.add(line)
                                lines.append(line)
                    return lines

                # Threaded for the same reason as L1: sqlite reads are blocking, and this one
                # fans out over several entities.
                for line in await asyncio.to_thread(_graph_hits):
                    hits.append({"layer": "L5b", "text": line, "score": 3.0, "source": "graph"})
            except Exception as e:  # noqa: BLE001
                logger.debug(f"fused_recall: L5b layer skipped ({e})")

        # Rank: score desc, then L1 before L2 before L5b before L3 (slight tiebreak by layer).
        # L5b outranks L3 on ties because a graph hit is an exact entity match, while a vault hit
        # is a substring match in a long note.
        layer_priority = {"L1": 0, "L2": 1, "L5b": 2, "L3": 3}
        hits.sort(key=lambda h: (-h["score"], layer_priority.get(h["layer"], 9)))
        return hits[:limit]

    def recent_digest(self, limit: int = 20) -> list[str]:
        """The most recently learned facts, newest first — injected into the system prompt."""
        notes = sorted(self._iter_notes(), key=lambda n: n.mtime, reverse=True)
        return [n.text for n in notes[:limit]]

    # Cue words that mark a learned fact as an open commitment / intention worth resurfacing later —
    # something the owner said he wanted or meant to do, which he may have let slip.
    _COMMIT_CUES = ("want to", "wants to", "planning", "plan to", "going to", "intend", "hoping to",
                    "would like to", "means to", "meant to", "need to", "should ", "someday",
                    "eventually", "thinking about", "keeps meaning", "on his list", "goal", "aims to")

    def salient_notes(self, *, now: datetime | None = None, limit: int = 5) -> list[dict]:
        """Durable commitments worth *proactively* resurfacing (Memory-util), ranked by salience.

        Salience = a commitment/intention cue (he said he wanted/meant to do it) weighted by a recency
        window that favours the sweet spot of a few days to a few weeks old: brand-new facts are still
        top-of-mind (no need to remind), and very old ones are stale. Returns [{text, created, age_days,
        note_id, score}], best first. Fail-quiet — a bad ``created`` timestamp just scores age-neutral.
        """
        now = now or datetime.now(timezone.utc)
        out: list[dict] = []
        for note in self._iter_notes():
            low = note.text.lower()
            cue = sum(1 for c in self._COMMIT_CUES if c in low)
            has_goal_tag = any(t in ("goal", "commitment", "todo", "intention") for t in note.tags)
            if not cue and not has_goal_tag:
                continue
            try:
                created = datetime.fromisoformat(note.created)
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                age_days = max(0.0, (now - created).total_seconds() / 86400.0)
            except (ValueError, TypeError):
                age_days = 7.0   # unknown age -> treat as mid-window, don't drop it
            # Recency weight: 0 under 1 day (too fresh), peak ~2–21 days, decaying after ~45 days.
            if age_days < 1:
                recency = 0.2
            elif age_days <= 21:
                recency = 1.0
            elif age_days <= 45:
                recency = 0.6
            else:
                recency = 0.25
            score = (cue + (1 if has_goal_tag else 0)) * recency
            out.append({"text": note.text, "created": note.created, "age_days": round(age_days, 1),
                        "note_id": note.path.stem, "score": round(score, 3)})
        out.sort(key=lambda d: -d["score"])
        return out[:limit]

    def forget(self, query: str) -> str | None:
        """Delete the single best-matching learned fact; return its text, or None."""
        terms = _terms(query)
        if not terms:
            return None
        best: tuple[int, Path, str] | None = None
        for note in self._iter_notes():
            low = note.text.lower()
            score = sum(low.count(t) for t in terms)
            if score > 0 and (best is None or score > best[0]):
                best = (score, note.path, note.text)
        if best is None:
            return None
        try:
            best[1].unlink()
            logger.info(f"memory: forgot {best[2][:50]!r}")
            return best[2]
        except OSError:
            return None

    def count(self) -> int:
        return sum(1 for _ in self._iter_notes())

    # ---- L2: daily journal ------------------------------------------------------------
    def journal_append(self, summary: str, when: datetime | None = None) -> Path | None:
        summary = (summary or "").strip()
        if not summary:
            return None
        when = when or datetime.now(timezone.utc)
        self.journal_dir.mkdir(parents=True, exist_ok=True)
        path = self.journal_dir / f"{when:%Y-%m-%d}.md"
        header = "" if path.exists() else f"# Journal — {when:%Y-%m-%d}\n\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{header}- **{when:%H:%M}** — {summary}\n")
        logger.info(f"memory: journalled {summary[:60]!r}")
        return path

    def read_journal(self, when: datetime | None = None) -> str:
        """Read a day's journal (default today); fall back to the most recent day."""
        if when is not None:
            p = self.journal_dir / f"{when:%Y-%m-%d}.md"
            return p.read_text(encoding="utf-8", errors="ignore").strip() if p.is_file() else ""
        if not self.journal_dir.is_dir():
            return ""
        days = sorted(self.journal_dir.glob("*.md"))
        return days[-1].read_text(encoding="utf-8", errors="ignore").strip() if days else ""


# A process-wide default store (the repo's memory/ dir).
STORE = MemoryStore()
