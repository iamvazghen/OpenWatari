"""Fold another host's learned memory into this one — the human half of 30.F1.

The laptop and the VPS both ran a brain, each writing learned facts under its own repo root, and
neither could see the other's. `shared/paths.py` stops that happening again; this closes the split
that already exists.

**The merge direction, and why it is not "newest wins".** For machine-written stores — vectors,
presence, coaching, the scheduler — the brain host is authoritative and the other copy is simply
stale: those stores are derived (an embedding cache, a rolling window), so picking the live one
loses nothing you cannot regenerate. Learned facts and journal entries are different in kind: a
fact only one host was ever told is not stale, it is the only copy. Choosing between hosts there
would silently delete something the owner said, which is the exact failure S30 exists to prevent.

So this script does not choose. It UNIONS the learned notes and appends the missing journal lines,
and it does it through `MemoryStore.remember()`, which already dedups by normalised text and
promotes a fact to a stronger source (30.F3) — so a fact the owner stated on one host stops being
recorded as something the other host merely inferred. Nothing is deleted, ever; the worst case is
a duplicate phrasing of something already known.

Copy the other host's memory directory down first (it is not deployed, by design):

    scp -r <brainhost>:~/.afon/memory /tmp/vps-memory
    uv run python scripts/merge_memory.py /tmp/vps-memory          # dry run — prints the plan
    uv run python scripts/merge_memory.py /tmp/vps-memory --apply
"""

from __future__ import annotations

import sys
from pathlib import Path

# The facts being merged are in six languages and a Windows console is cp1252, so printing one
# raised UnicodeEncodeError *after* the merge had already written everything: a completed merge
# that exits non-zero and reports nothing, which reads exactly like a failed one.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.memory import MemoryStore, _norm, source_rank   # noqa: E402
from afon.shared.paths import memory_dir                        # noqa: E402


def merge(other: Path, into: Path, *, apply: bool = False) -> tuple[list[str], list[str]]:
    """Union `other` into `into`. Returns (facts, journal-days) that were (or would be) added."""
    src, dst = MemoryStore(other), MemoryStore(into)
    known = {_norm(n.text): n for n in dst._iter_notes()}

    facts: list[str] = []
    for note in src._iter_notes():
        key = _norm(note.text)
        seen = known.get(key)
        if seen is not None and source_rank(note.source) <= source_rank(seen.source):
            continue
        facts.append(f"{'+' if seen is None else '^'} [{note.source}] {note.text[:90]}")
        if apply:
            # remember() handles both cases: a new fact is written, a known one is promoted if the
            # incoming source is stronger. Passing the note's own provenance keeps the merge honest
            # — an inferred fact stays inferred rather than becoming "the owner said so".
            dst.remember(note.text, note.tags, source=note.source, confidence=note.confidence)

    days: list[str] = []
    src_journal, dst_journal = other / "journal", into / "journal"
    for day in sorted(src_journal.glob("*.md")) if src_journal.is_dir() else []:
        target = dst_journal / day.name
        incoming = day.read_text(encoding="utf-8", errors="ignore").splitlines()
        have = set(target.read_text(encoding="utf-8", errors="ignore").splitlines()) \
            if target.is_file() else set()
        # The provenance marker this script writes is metadata, not journal content. Counting it as
        # a missing line makes two hosts merging in both directions append a marker about each
        # other's marker, forever — the merge never converges and every day file grows on every run.
        missing = [ln for ln in incoming
                   if ln.strip() and not ln.lstrip().startswith("<!--") and ln not in have]
        if not missing:
            continue
        days.append(f"{day.name}: +{len(missing)} line(s)")
        if apply:
            dst_journal.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fh:
                if have:
                    fh.write(f"\n<!-- merged from {other} -->\n")
                fh.write("\n".join(missing) + "\n")
    return facts, days


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    if len(args) != 1:
        print(__doc__)
        return 2
    other = Path(args[0]).expanduser().resolve()
    into = memory_dir()
    if not other.is_dir():
        print(f"not a directory: {other}")
        return 2
    if other == into.resolve():
        print("source and destination are the same directory")
        return 2

    facts, days = merge(other, into, apply=apply)
    print(f"{'MERGED' if apply else 'DRY RUN'}  {other}  ->  {into}")
    for line in facts:
        print(f"  {line}")
    for line in days:
        print(f"  journal {line}")
    print(f"\n{len(facts)} fact(s) {'added/promoted' if apply else 'would be added/promoted'}, "
          f"{len(days)} journal day(s) touched.")
    if not apply and (facts or days):
        print("Re-run with --apply to write. Nothing is ever deleted or overwritten.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
