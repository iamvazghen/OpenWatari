"""What Afon knows about the owner, where it is, and how long he keeps it (37.F1).

"What do you know about me?" had no answer. There are two dozen stores under the state root, each
created by whichever module needed it, and the only place their existence was written down was the
code that opened them. A hand-written privacy document would have been wrong the week after it was
written — that is exactly what the plan declined — so this is generated: the declarations below live
next to the stores they describe, and the gate walks the state root and FAILS when something on disk
is not declared. A new store cannot be added quietly; it can only be added and described.

Three columns matter and only three:

  * **what it holds** — in the owner's terms, not the schema's. "Every tool call Afon made" beats
    "audit rows".
  * **how long** — a number of days, or KEEP_FOREVER said out loud. A store with no stated retention
    is a store that grows until a disk fills, and "we'll decide later" is how that happens.
  * **who can read it** — the honest answer for most of these is "this host", and for two of them it
    is not, which is the reason the column exists.

    uv run python -m afon.brain.inventory          # the spoken report
    uv run python -m afon.brain.inventory --json   # the rows
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from loguru import logger

from afon.shared.paths import audit_dir, memory_dir, state_dir, store_path

#: A store the owner has decided to keep indefinitely. Spelled out rather than left as None, because
#: a missing retention and a deliberate forever look identical in a table and mean opposite things.
KEEP_FOREVER = -1

#: One document that is replaced in place — the current plan, the current session, today's digest
#: marker. It does not accumulate, so it has no retention in days, and a day count here would be a
#: policy the sweep must refuse to enforce: deleting approvals.json because nothing wrote to it for
#: a month throws away pending approvals, and the same rule would remove the live pid files under
#: run/. A dry run of the first version of the sweep proposed exactly that. Its lifetime is "while
#: it is the current state", which is a real answer and not a missing one.
WHILE_CURRENT = 0

#: Who can read a store. THIS_HOST is the honest answer for nearly everything; the exceptions are
#: what makes the column worth printing.
THIS_HOST = "this host only"
OWNER_VAULT = "this host + the Obsidian vault (which syncs to the VPS)"
BIOMETRIC = "this host only — never leaves the laptop, by decision"


@dataclass(frozen=True)
class Store:
    name: str
    holds: str
    retention_days: int
    readable_by: str = THIS_HOST
    #: Relative to the state root, or absolute for the two that live elsewhere. A directory entry
    #: covers everything under it.
    where: str = ""

    def path(self) -> Path:
        if not self.where:
            return state_dir() / self.name
        p = Path(self.where)
        return p if p.is_absolute() else state_dir() / self.where

    @property
    def forever(self) -> bool:
        return self.retention_days == KEEP_FOREVER

    @property
    def rolling(self) -> bool:
        """True when the store ACCUMULATES, so age is a meaningful thing to sweep on."""
        return self.retention_days > 0

    def size_bytes(self) -> int:
        p = self.path()
        try:
            if p.is_dir():
                return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
            return p.stat().st_size if p.exists() else 0
        except OSError:
            return 0


#: Every store, declared beside nothing and therefore in one place. Retention numbers are the
#: defaults; `settings` overrides where one already exists (presence has had one since Phase 0).
STORES: tuple[Store, ...] = (
    # --- the memory layers -----------------------------------------------------------------------
    Store("learned facts (L1)", "things Afon has been told or inferred about the owner",
          KEEP_FOREVER, THIS_HOST, where=str(memory_dir() / "learned")),
    Store("journal (L2)", "a short daily record of what happened", 35, THIS_HOST,
          where=str(memory_dir() / "journal")),
    Store("afon_vectors.sqlite", "embeddings of the learned facts, for semantic recall",
          KEEP_FOREVER, THIS_HOST, where=str(store_path("vectors"))),
    Store("afon_graph.sqlite", "who and what relate to whom — people, projects, places",
          KEEP_FOREVER, THIS_HOST, where=str(store_path("graph"))),
    # --- what he observed ------------------------------------------------------------------------
    Store("afon_presence.sqlite", "which app was in the foreground, and how idle the machine was",
          30, THIS_HOST, where=str(store_path("presence"))),
    Store("patterns.jsonl", "recurring habits derived from the above", 180),
    Store("world_model.json", "the owner's goals, projects and deadlines", KEEP_FOREVER),
    Store("relationship.json", "sensitivities, running jokes, how the two of them stand", KEEP_FOREVER),
    Store("room_context.json", "who else has been in the room", WHILE_CURRENT),
    # --- what he did -----------------------------------------------------------------------------
    Store("audit", "every tool call, with values scrubbed of secrets", 90, THIS_HOST,
          where=str(audit_dir())),
    Store("traces", "one row per turn: tools fired, timings, sources read", 30),
    Store("recent_actions.json", "the last few actions, so they can be undone", WHILE_CURRENT),
    Store("tool_usage.json", "how often each tool is reached for", WHILE_CURRENT),
    Store("delivery_ledger.json", "whether a message was delivered, seen, or acted on", WHILE_CURRENT),
    # --- what he is meant to do ------------------------------------------------------------------
    Store("afon_tasks.sqlite", "the to-do list and its deadlines", KEEP_FOREVER),
    Store("afon_jobs.sqlite", "background work queued and finished", 30),
    Store("afon_work.sqlite", "work handed to the fleet or a worker, and what came back", 90),
    Store("documents", "documents Afon wrote here, and an index of every one he wrote anywhere",
          KEEP_FOREVER),
    Store("afon_coaching.sqlite", "progress on the skills he is being coached in", KEEP_FOREVER),
    Store("objectives.json", "multi-day objectives Afon is driving", KEEP_FOREVER),
    Store("approvals.json", "outward actions waiting for the owner's yes", WHILE_CURRENT),
    Store("routines.json", "when Afon expects the owner to be doing what", KEEP_FOREVER),
    Store("routines_draft.json", "routines drafted from evidence, awaiting correction", WHILE_CURRENT),
    Store("day_plan.json", "today's plan as Afon understands it", WHILE_CURRENT),
    # --- identity ---------------------------------------------------------------------------------
    Store("voiceprint.json", "the owner's voice embedding, for recognising him", KEEP_FOREVER,
          BIOMETRIC),
    Store("faces", "the owner's face embedding, for recognising him", KEEP_FOREVER, BIOMETRIC),
    # --- plumbing that still holds personal data --------------------------------------------------
    Store("proactive_state.json", "what Afon has already said unprompted, so he does not repeat", WHILE_CURRENT),
    Store("resurfaced_memories.json", "which memories have been brought up lately", WHILE_CURRENT),
    Store("daily_digest_state.json", "whether today's catch-up has been given", WHILE_CURRENT),
    Store("afon_session.json", "the current conversation's id", WHILE_CURRENT),
    Store("conversation_id", "the current conversation's id", WHILE_CURRENT),
    Store("runtime_prefs.json", "runtime switches the owner has flipped", KEEP_FOREVER),
    Store("audio_pref.json", "which speaker he wants Afon to talk through", KEEP_FOREVER),
    Store("composio_catalog.json", "a cached list of the external apps available", WHILE_CURRENT),
    Store("health_probe.json", "the last self-check result", WHILE_CURRENT),
    Store("health_escalation.json", "whether the owner has been paged about an outage", WHILE_CURRENT),
    Store("restore_drill.json", "when a backup was last proved restorable", KEEP_FOREVER),
    Store("screenshots", "the last screen capture, for OCR", WHILE_CURRENT),
    Store("run", "process ids and locks", WHILE_CURRENT),
    Store("memory", "the memory layers above live here", KEEP_FOREVER),
    Store("MAINTENANCE", "a marker that says Afon is parked", KEEP_FOREVER),
    # Found by the undeclared check on its first run, which is the point of having it. The error
    # journal holds the tail of whatever a failing tool was handed, so it is personal data whether
    # or not it was meant to be — that is why it expires rather than being kept.
    Store("errors.jsonl", "what failed, across every process, with secrets scrubbed", 30),
    Store("MIGRATED.txt", "a note that the one-shot move out of the repo has run", KEEP_FOREVER),
    Store("voiceprint.json.bak", "the previous voice embedding, kept through a re-enrolment",
          KEEP_FOREVER, BIOMETRIC),
)


def by_name(name: str) -> Store | None:
    return next((s for s in STORES if s.name == name), None)


def declared_names() -> set[str]:
    """Top-level entries under the state root that the inventory accounts for."""
    root = state_dir()
    out: set[str] = set()
    for s in STORES:
        p = s.path()
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue          # audit/ and memory/ can be configured outside the state root
        out.add(rel.parts[0])
    return out


def undeclared() -> list[str]:
    """Entries on disk under the state root that nothing in STORES describes (37.F1's whole point)."""
    root = state_dir()
    if not root.is_dir():
        return []
    known = declared_names()
    return sorted(p.name for p in root.iterdir()
                  if p.name not in known and not p.name.startswith("."))


def rolling_stores() -> list[Store]:
    """The stores a retention sweep may touch: the ones that accumulate files."""
    return [s for s in STORES if retention_days(s) > 0]


def retention_days(store: Store) -> int:
    """The store's retention, honouring a setting where one already exists."""
    from afon.config import settings

    if store.name.startswith("afon_presence"):
        return int(getattr(settings, "presence_retention_days", store.retention_days) or
                   store.retention_days)
    return store.retention_days


def rows() -> list[dict]:
    """The inventory, as data. `present` says whether the store actually exists on this host."""
    out = []
    for s in STORES:
        d = asdict(s)
        d["retention_days"] = retention_days(s)
        d["path"] = str(s.path())
        d["present"] = s.path().exists()
        d["size_bytes"] = s.size_bytes()
        out.append(d)
    return out


def spoken() -> str:
    """The five-minute privacy answer, short enough to say out loud."""
    live = [r for r in rows() if r["present"]]
    if not live:
        return "I'm not storing anything about you on this host yet, sir."
    forever = [r for r in live if r["retention_days"] == KEEP_FOREVER]
    expiring = [r for r in live if r["retention_days"] > 0]
    total_mb = sum(r["size_bytes"] for r in live) / 1_000_000
    bio = [r for r in live if r["readable_by"] == BIOMETRIC]
    extra = undeclared()
    said = (f"I keep {len(live)} stores about you on this host, sir — {total_mb:.1f} megabytes. "
            f"{len(expiring)} of them expire on their own; {len(forever)} I keep indefinitely, and "
            f"the rest are single documents I replace as they change. "
            f"{len(bio)} hold biometrics and never leave this laptop.")
    if extra:
        # The one thing this report must never do: imply it is complete when it is not.
        said += (f" There {'is' if len(extra) == 1 else 'are'} also {len(extra)} thing"
                 f"{'' if len(extra) == 1 else 's'} on disk I can't account for: "
                 f"{', '.join(extra[:4])}.")
    return said


def report() -> str:
    """The long form — one line per store, for reading rather than hearing."""
    lines = [f"{'store':38} {'kept':>10}  {'size':>9}  holds"]
    for r in sorted(rows(), key=lambda r: (not r["present"], r["name"])):
        kept = {KEEP_FOREVER: "forever", WHILE_CURRENT: "while current"}.get(
            r["retention_days"], f"{r['retention_days']}d")
        size = f"{r['size_bytes'] / 1000:.0f} kB" if r["present"] else "-"
        mark = " " if r["present"] else "-"
        lines.append(f"{mark}{r['name']:37} {kept:>10}  {size:>9}  {r['holds']}")
    extra = undeclared()
    if extra:
        lines.append("")
        lines.append("NOT DECLARED (add to inventory.STORES): " + ", ".join(extra))
    return "\n".join(lines)


def _selfcheck() -> None:
    assert len({s.name for s in STORES}) == len(STORES), "a store is declared twice"
    for s in STORES:
        assert s.holds and not s.holds.endswith("."), f"{s.name}: `holds` reads as a phrase"
        assert s.retention_days in (KEEP_FOREVER, WHILE_CURRENT) or s.retention_days > 0, s.name
        assert s.rolling == (s.retention_days > 0), s.name
        assert s.readable_by in (THIS_HOST, OWNER_VAULT, BIOMETRIC), s.name
    said = spoken()
    assert "sir" in said and "megabytes" in said or "not storing" in said, said
    assert "store" in report()
    logger.debug(f"inventory: {len(STORES)} stores declared")
    print(f"inventory self-check OK ({len(STORES)} stores, "
          f"{len([s for s in STORES if s.path().exists()])} present)")


if __name__ == "__main__":
    if "--json" in sys.argv:
        print(json.dumps(rows(), indent=2))
    elif "--selfcheck" in sys.argv:
        _selfcheck()
    else:
        print(spoken())
        print()
        print(report())
