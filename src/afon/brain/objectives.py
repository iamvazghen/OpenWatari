"""Phase 4.1 — persistent multi-day objectives Afon OWNS and drives.

The daily backlog pass (Phase 3.1) reacts to Notion tasks; this goes a step further into agency: the
owner explicitly HANDS Afon a goal to carry across days — "get the Party Map beta launch-ready". Afon
records it, advances the SAFE parts one step each day via the bounded worker (which defers every
outward/destructive step for approval), keeps a dated progress log, and reports unprompted. Durable JSON,
fail-quiet — an error in here never reaches a live turn.

Deliberately separate from the world-model (WORLD): that models what the owner is doing (for
anticipation); this is the short list of objectives Afon is actively DRIVING (for agency).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:48] or "objective"


@dataclass
class Objective:
    id: str
    text: str
    status: str = "active"        # active | done | dropped
    project: str = ""
    created: str = field(default="")
    updated: str = field(default="")
    progress: list = field(default_factory=list)   # [{ts, note}] — dated log of daily advances
    deferred: list = field(default_factory=list)    # outstanding owner-approval steps (deduped)

    def __post_init__(self) -> None:
        if not self.created:
            self.created = _now().isoformat(timespec="seconds")
        if not self.updated:
            self.updated = self.created


class ObjectiveBook:
    """Durable list of objectives Afon is driving. One per brain; ``path`` injectable for tests."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else Path.home() / ".afon" / "objectives.json"
        self._items: dict[str, Objective] = {}
        self._load()

    # ---- persistence -------------------------------------------------------------------------
    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for o in raw.get("objectives", []):
                try:
                    obj = Objective(
                        id=o["id"], text=o.get("text", ""), status=o.get("status", "active"),
                        project=o.get("project", ""), created=o.get("created", ""),
                        updated=o.get("updated", ""),
                        progress=[p for p in o.get("progress", []) if isinstance(p, dict)],
                        deferred=[d for d in o.get("deferred", []) if isinstance(d, str)],
                    )
                    self._items[obj.id] = obj
                except (KeyError, TypeError):
                    continue
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001 — a corrupt file starts empty, never crashes the brain
            logger.warning(f"objectives: load failed ({type(e).__name__}); starting empty")

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"objectives": [asdict(o) for o in self._items.values()]}
            self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"objectives: save failed ({type(e).__name__})")

    # ---- mutation ----------------------------------------------------------------------------
    def assign(self, text: str, *, project: str = "") -> Objective:
        """Take on an objective. Same slug re-activates (never duplicates)."""
        text = (text or "").strip()
        oid = _slug(text)
        obj = self._items.get(oid)
        if obj:
            obj.text = text or obj.text
            obj.status = "active"
            obj.project = project or obj.project
            obj.updated = _now().isoformat(timespec="seconds")
        else:
            obj = Objective(id=oid, text=text, project=project)
            self._items[oid] = obj
        self._save()
        return obj

    def get(self, id: str) -> Objective | None:
        return self._items.get(id)

    def find(self, topic: str) -> Objective | None:
        """Resolve ONE active objective by id or word match; None if 0 or >1 match (ambiguous)."""
        t = (topic or "").strip().lower()
        if not t:
            return None
        exact = self._items.get(t)
        if exact and exact.status == "active":
            return exact
        hits = [o for o in self._items.values() if o.status == "active" and t in o.text.lower()]
        return hits[0] if len(hits) == 1 else None

    def active(self) -> list[Objective]:
        """Active objectives, least-recently-advanced first (so the daily driver is fair across them)."""
        return sorted((o for o in self._items.values() if o.status == "active"), key=lambda o: o.updated)

    def set_status(self, id: str, status: str) -> bool:
        o = self._items.get(id)
        if not o:
            return False
        o.status = status
        o.updated = _now().isoformat(timespec="seconds")
        self._save()
        return True

    def append_progress(self, id: str, note: str, *, deferred: list[str] | None = None) -> None:
        o = self._items.get(id)
        if not o:
            return
        o.progress.append({"ts": _now().isoformat(timespec="seconds"), "note": (note or "").strip()})
        o.progress = o.progress[-20:]  # keep the last 20 daily entries
        for d in deferred or []:
            if d and d not in o.deferred:
                o.deferred.append(d)
        o.updated = _now().isoformat(timespec="seconds")
        self._save()

    # ---- read --------------------------------------------------------------------------------
    def render(self, max_items: int = 8) -> str:
        """Compact status text (for a spoken 'what are you working on' or the prompt). '' when empty."""
        act = self.active()[:max_items]
        if not act:
            return ""
        lines = ["# Objectives Afon is driving"]
        for o in act:
            last = o.progress[-1]["note"] if o.progress else "not started yet"
            proj = f"[{o.project}] " if o.project else ""
            lines.append(f"- {proj}{o.text} — latest: {last}")
            if o.deferred:
                lines.append(f"  · awaiting your approval: {'; '.join(o.deferred[:3])}")
        return "\n".join(lines)


# Process-wide singleton (mirrors WORLD / STORE / TASKS). Tests build their own with a temp path.
OBJECTIVES = ObjectiveBook()


# ---- the daily driver ------------------------------------------------------------------------

def _objective_brief(obj: Objective) -> str:
    log = "\n".join(f"- ({p['ts'][:10]}) {p['note']}" for p in obj.progress[-5:]) or "- (nothing yet)"
    return (
        "Advance this multi-day objective for the owner. Make concrete progress on the NEXT safe step "
        "only — do not try to finish everything at once, and don't repeat a step already logged below.\n\n"
        f"OBJECTIVE: {obj.text}\n\nPROGRESS SO FAR:\n{log}\n\n"
        "Do the safe research/draft/coding for the next step yourself. If a step needs the owner "
        "(sending, publishing, spending money, deleting), DEFER it. End with a ONE-LINE summary of what "
        "you advanced today, then a 'Needs your approval:' list if any outward step remains."
    )


def _split_result(result: str) -> tuple[str, list[str]]:
    """Split the worker's text into (progress summary, deferred approval items)."""
    text = (result or "").strip()
    deferred: list[str] = []
    marker = "Needs your approval:"
    if marker in text:
        head, tail = text.split(marker, 1)
        text = head.strip()
        deferred = [d.strip(" .") for d in tail.split(";") if d.strip(" .")]
    return (text or "made some progress"), deferred


async def advance_objectives(
    llm: Any,
    registry: dict[str, Callable[[dict], Awaitable[str]]],
    tools: list[dict[str, Any]],
    *,
    max_objectives: int = 2,
    max_steps: int = 6,
    book: ObjectiveBook | None = None,
    worker_factory: Callable[[Objective], Any] | None = None,
) -> list[dict]:
    """Advance the top active objectives one safe step each and log progress. Returns
    ``[{id, text, summary}]`` for what moved. ``book`` and ``worker_factory`` are injectable for tests.
    Safe by construction — the bounded worker defers every outward step (queued for the owner's approval,
    tagged with this objective as its origin). Never raises into the loop."""
    from afon.brain.worker import TaskWorker

    book = book or OBJECTIVES
    make = worker_factory or (
        lambda o: TaskWorker(llm, registry, tools, max_steps=max_steps, origin=f"objective:{o.id}")
    )
    out: list[dict] = []
    for obj in book.active()[:max_objectives]:
        try:
            result = await make(obj).run(_objective_brief(obj))
        except Exception as e:  # noqa: BLE001 — one objective failing must not stop the others
            logger.warning(f"objective '{obj.id}' advance failed: {type(e).__name__}")
            continue
        summary, deferred = _split_result(result)
        book.append_progress(obj.id, summary, deferred=deferred)
        out.append({"id": obj.id, "text": obj.text, "summary": summary})
    return out


def spoken_objectives_report(advanced: list[dict]) -> str:
    """One proactive line summarising what got advanced overnight. '' when nothing moved."""
    if not advanced:
        return ""
    if len(advanced) == 1:
        a = advanced[0]
        return f"I made some headway on your objective overnight, sir — {a['text']}: {a['summary']}"
    parts = "; ".join(f"{a['text']} — {a['summary']}" for a in advanced)
    return f"I advanced a few of your objectives overnight, sir. {parts}"
