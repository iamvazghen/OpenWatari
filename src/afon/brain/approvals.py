"""Phase 4.2 — the approval queue: deferred outward steps become APPROVABLE actions.

The bounded worker is safe by construction: it refuses to send email, publish, spend, or delete while
running unattended, and notes the step instead. Until now that note was only TEXT — the owner could read
"Needs your approval: send_email(...)" but not act on it, so autonomous work dead-ended at the first
outward step.

This turns each deferred step into a real, replayable action: the exact tool + args are queued, the owner
can list them and say "approve the first one", and Afon executes it THEN — with the human in the loop
where it belongs. That's the difference between a worker that stalls and a copilot that hands you a
decision. Durable JSON, fail-quiet; enqueueing must never break a running task.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from loguru import logger
from afon.shared.paths import state_dir


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _short_id() -> str:
    return uuid.uuid4().hex[:6]


def describe(tool: str, args: dict) -> str:
    """Human-readable one-liner for an action, e.g. ``send_email(to=x, subject=y)``."""
    bits = ", ".join(f"{k}={v}" for k, v in list((args or {}).items())[:3])
    return f"{tool}({bits})"


@dataclass
class Approval:
    id: str
    tool: str
    args: dict = field(default_factory=dict)
    origin: str = ""              # what produced it, e.g. "objective:get-party-map-launch-ready"
    status: str = "pending"       # pending | approved | rejected | failed
    created: str = field(default="")
    resolved: str = ""
    result: str = ""

    def __post_init__(self) -> None:
        if not self.created:
            self.created = _now().isoformat(timespec="seconds")

    @property
    def summary(self) -> str:
        return describe(self.tool, self.args)


class ApprovalQueue:
    """Durable queue of outward actions awaiting the owner's yes/no. ``path`` injectable for tests."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else state_dir() / "approvals.json"
        self._items: dict[str, Approval] = {}
        self._load()

    # ---- persistence -------------------------------------------------------------------------
    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            for a in raw.get("approvals", []):
                try:
                    ap = Approval(
                        id=a["id"], tool=a.get("tool", ""), args=a.get("args") or {},
                        origin=a.get("origin", ""), status=a.get("status", "pending"),
                        created=a.get("created", ""), resolved=a.get("resolved", ""),
                        result=a.get("result", ""),
                    )
                    self._items[ap.id] = ap
                except (KeyError, TypeError):
                    continue
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001 — a corrupt file starts empty, never crashes the brain
            logger.warning(f"approvals: load failed ({type(e).__name__}); starting empty")

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"approvals": [asdict(a) for a in self._items.values()]}
            self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"approvals: save failed ({type(e).__name__})")

    # ---- mutation ----------------------------------------------------------------------------
    def enqueue(self, tool: str, args: dict, *, origin: str = "") -> Approval | None:
        """Queue a deferred outward action. Deduped on (tool, args, origin) while still pending, so a
        worker retrying the same step doesn't stack duplicates. Returns the Approval (or None if unusable)."""
        tool = (tool or "").strip()
        if not tool:
            return None
        args = args or {}
        for a in self._items.values():
            if a.status == "pending" and a.tool == tool and a.args == args and a.origin == origin:
                return a
        ap = Approval(id=_short_id(), tool=tool, args=args, origin=origin)
        self._items[ap.id] = ap
        self._save()
        return ap

    def pending(self) -> list[Approval]:
        """Oldest first — 'approve the first one' means the one that has waited longest."""
        return sorted((a for a in self._items.values() if a.status == "pending"), key=lambda a: a.created)

    def find(self, topic: str) -> Approval | None:
        """Resolve ONE pending approval by id, ordinal ('1'/'first'), or a word in the tool/args."""
        t = (topic or "").strip().lower()
        pend = self.pending()
        if not pend:
            return None
        if not t or t in ("first", "1", "one", "it", "that", "the first"):
            return pend[0]
        if t in ("last", "latest"):
            return pend[-1]
        exact = self._items.get(t)
        if exact and exact.status == "pending":
            return exact
        hits = [a for a in pend if t in a.summary.lower()]
        return hits[0] if len(hits) == 1 else None

    def reject(self, id: str, reason: str = "") -> bool:
        a = self._items.get(id)
        if not a or a.status != "pending":
            return False
        a.status = "rejected"
        a.result = reason or "declined by the owner"
        a.resolved = _now().isoformat(timespec="seconds")
        self._save()
        return True

    async def approve(
        self,
        id: str,
        registry: dict[str, Callable[[dict], Awaitable[str]]] | None = None,
    ) -> tuple[bool, str]:
        """Execute a pending action NOW (the owner said yes) and record the outcome.
        Returns ``(ok, result_text)``. Never raises — a failing tool marks the item 'failed'."""
        a = self._items.get(id)
        if not a or a.status != "pending":
            return False, "that action isn't pending any more, sir"
        if registry is None:
            from afon.brain.tools import tool_handlers

            registry = tool_handlers()
        fn = registry.get(a.tool)
        if fn is None:
            a.status = "failed"
            a.result = f"no handler for {a.tool}"
            a.resolved = _now().isoformat(timespec="seconds")
            self._save()
            return False, f"I no longer have a tool called {a.tool}, sir."
        try:
            out = str(await fn(a.args))
            a.status, a.result = "approved", out
        except Exception as e:  # noqa: BLE001 — a failing approved action is recorded, not raised
            logger.warning(f"approval {id} ({a.tool}) failed: {type(e).__name__}")
            a.status, a.result = "failed", f"{type(e).__name__}: {e}"
            a.resolved = _now().isoformat(timespec="seconds")
            self._save()
            return False, f"I tried {a.summary}, sir, but it failed: {type(e).__name__}."
        a.resolved = _now().isoformat(timespec="seconds")
        self._save()
        return True, out

    def prune(self, keep: int = 50) -> int:
        """Drop the oldest resolved items beyond ``keep``. Returns how many were removed."""
        resolved = sorted((a for a in self._items.values() if a.status != "pending"),
                          key=lambda a: a.resolved or a.created)
        drop = resolved[:-keep] if len(resolved) > keep else []
        for a in drop:
            del self._items[a.id]
        if drop:
            self._save()
        return len(drop)

    # ---- read --------------------------------------------------------------------------------
    def render(self, max_items: int = 8) -> str:
        """Spoken-friendly list of what's waiting on the owner. '' when nothing is pending."""
        pend = self.pending()[:max_items]
        if not pend:
            return ""
        lines = []
        for i, a in enumerate(pend, 1):
            where = f" (from {a.origin})" if a.origin else ""
            lines.append(f"{i}. [{a.id}] {a.summary}{where}")
        return "\n".join(lines)


# Process-wide singleton. Tests build their own with a temp path.
APPROVALS = ApprovalQueue()
