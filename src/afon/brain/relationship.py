"""Phase 6.2 — relationship memory: the layer above facts.

Facts (L1) know the owner runs a rabbit farm. This knows the *relationship*: how he's been feeling over
time (so Afon can notice "you've seemed stressed all week"), topics to handle gently (sensitivities),
and the running jokes / shared references that make continuity feel personal instead of transactional.

Durable JSON, fail-quiet (a corrupt file starts empty, never crashes a turn). The affect log is fed
automatically each turn by the affect reader (Phase 6.1); sensitivities and running jokes are added by the
owner ("that's a sore subject", "that's our running joke") or by the background reviewer.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Relationship:
    affect_log: list = field(default_factory=list)   # [{ts, tags:[...]}] rolling, capped
    sensitivities: list = field(default_factory=list)  # topics to handle gently
    running_jokes: list = field(default_factory=list)  # shared references / inside jokes


class RelationshipMemory:
    """Durable store of the owner relationship. One per brain; ``path`` injectable for tests."""

    _AFFECT_CAP = 60

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else Path.home() / ".afon" / "relationship.json"
        self._r = Relationship()
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._r = Relationship(
                affect_log=[a for a in raw.get("affect_log", []) if isinstance(a, dict)][-self._AFFECT_CAP:],
                sensitivities=[s for s in raw.get("sensitivities", []) if isinstance(s, str)],
                running_jokes=[j for j in raw.get("running_jokes", []) if isinstance(j, str)],
            )
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001 — a corrupt file starts empty, never crashes the brain
            logger.warning(f"relationship: load failed ({type(e).__name__}); starting empty")

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(asdict(self._r), indent=2), encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"relationship: save failed ({type(e).__name__})")

    # ---- mutation ----------------------------------------------------------------------------
    def note_affect(self, tags: list[str], *, now: datetime | None = None) -> None:
        """Append one affect observation (skips empty/neutral so the log stays meaningful)."""
        tags = [t for t in (tags or []) if t]
        if not tags:
            return
        self._r.affect_log.append({"ts": (now or _now()).isoformat(timespec="seconds"), "tags": tags})
        self._r.affect_log = self._r.affect_log[-self._AFFECT_CAP:]
        self._save()

    def add_sensitivity(self, topic: str) -> bool:
        topic = (topic or "").strip()
        if not topic or topic.lower() in {s.lower() for s in self._r.sensitivities}:
            return False
        self._r.sensitivities.append(topic)
        self._save()
        return True

    def add_running_joke(self, joke: str) -> bool:
        joke = (joke or "").strip()
        if not joke or joke.lower() in {j.lower() for j in self._r.running_jokes}:
            return False
        self._r.running_jokes.append(joke)
        self._save()
        return True

    # ---- read --------------------------------------------------------------------------------
    def recent_mood(self, last: int = 12) -> str:
        """A one-word read of the recent affect trend, or '' if there's not enough signal."""
        recent = self._r.affect_log[-last:]
        if len(recent) < 3:
            return ""
        counts: Counter = Counter()
        for e in recent:
            counts.update(e.get("tags", []))
        # A mood only "sticks" if it shows up across a meaningful share of recent turns.
        threshold = max(2, len(recent) // 3)
        for mood in ("stress", "tired", "low"):
            if counts.get(mood, 0) >= threshold:
                return {"stress": "stressed", "tired": "worn down", "low": "low"}[mood]
        if counts.get("upbeat", 0) >= threshold:
            return "in good spirits"
        return ""

    def render(self) -> str:
        """Compact prompt note about the relationship, or '' when there's nothing worth saying."""
        lines: list[str] = []
        mood = self.recent_mood()
        if mood:
            lines.append(f"He's seemed {mood} across recent conversations — factor that into your tone.")
        if self._r.sensitivities:
            lines.append("Handle these topics gently: " + "; ".join(self._r.sensitivities[:5]) + ".")
        if self._r.running_jokes:
            lines.append("Shared references you can call back to: " + "; ".join(self._r.running_jokes[:5]) + ".")
        return "\n".join(lines)


# Process-wide singleton (mirrors WORLD / OBJECTIVES). Tests build their own with a temp path.
RELATIONSHIP = RelationshipMemory()
