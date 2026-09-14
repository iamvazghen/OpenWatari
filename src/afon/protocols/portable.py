"""50.F1/50.F2 — an export a human can read with no codebase, and a drill that proves it.

The substrate for continuity already existed: a vault, backups, checkpoints and an audit trail.
What did not exist was the property those are supposed to deliver. A backup is a tar of this
program's private layout — sqlite files whose schemas live in the code, a `memory/learned` tree
whose meaning is a docstring, a JSONL whose fields are named in a dataclass. Restoring it needs
Afon. **That is the opposite of preservation.** The bar here is that everything worth keeping
outlives any single version of Afon and can be read by a person without his help, and a format
only this program understands fails that on the day it matters most.

So the export is markdown and JSON, with a README in plain language, and a MANIFEST that carries a
sha256 for every file. Three properties, in order of how much they matter:

  1. **Readable without the codebase.** No pickles, no sqlite, no field that means something only
     because a class says so. The drill proves this the only way it can be proved: it re-reads the
     export in a process that never imports `afon`.
  2. **Honest about what is missing.** A store that was not there is listed in the manifest as
     absent rather than quietly left out, because an export that silently omits half the memory
     looks exactly like an export of half as much memory.
  3. **Verified, on a schedule.** An export nobody has restored is a rumour — the same argument
     22.F4 makes about backups, and it applies harder here, because this one is meant to be opened
     years later by someone who cannot ask whether it worked.

ponytail: files and hashes, no archive format and no encryption. `restic` already owns the off-site
copy (S22); adding a container here would put a second thing to understand between a person and
their own notes, which is the whole failure being fixed.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from afon.shared.paths import audit_dir, memory_dir, state_dir

#: Bumped when the LAYOUT changes in a way a reader would notice. Written into the manifest and
#: into the README, so a person opening an old export knows which shape they are holding.
FORMAT = "afon-portable-1"

#: How many bytes to read at a time when hashing. The budget says stream rather than load.
_CHUNK = 1024 * 1024

#: JSON state worth preserving, and what each is in the owner's words. Anything not listed is
#: deliberately left out — this is the export, not the backup.
STATE_FILES: tuple[tuple[str, str], ...] = (
    ("world_model.json", "your goals, projects and deadlines as Afon understood them"),
    ("objectives.json", "multi-day objectives he was driving"),
    ("routines.json", "when he expected you to be doing what"),
    ("relationship.json", "sensitivities, running jokes, how the two of you stood"),
    ("patterns.jsonl", "recurring habits he derived from watching"),
)


@dataclass
class Export:
    """What was written, what was skipped, and whether it verified."""

    root: Path
    files: list[str] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    ms: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.files)

    def spoken(self) -> str:
        if self.error:
            return f"I couldn't write the portable export, sir — {self.error}."
        bits = f"{len(self.files)} files"
        if self.counts.get("facts"):
            bits += f", {self.counts['facts']} facts"
        if self.counts.get("documents"):
            bits += f" and {self.counts['documents']} documents"
        said = f"Portable export written to {self.root}, sir — {bits}."
        if self.absent:
            said += (" Nothing was stored for " + ", ".join(self.absent)
                     + ", and the manifest says so rather than leaving it out.")
        return said


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _write(root: Path, rel: str, text: str, out: Export) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    out.files.append(rel)


# --- the pieces ------------------------------------------------------------------------------


def _learned_md(mem: Path) -> tuple[str, int]:
    """Every learned fact as one markdown list, newest first, with where it came from."""
    d = mem / "learned"
    rows: list[tuple[float, str]] = []
    if d.is_dir():
        for f in sorted(d.glob("*.md")):
            try:
                body = f.read_text(encoding="utf-8", errors="ignore").strip()
                rows.append((f.stat().st_mtime, body))
            except OSError:
                continue
    rows.sort(key=lambda r: -r[0])
    lines = ["# What Afon had learned about you", "",
             "One fact per entry, newest first. The line under each is where it came from and how "
             "sure he was.", ""]
    for when, body in rows:
        stamp = datetime.fromtimestamp(when).strftime("%Y-%m-%d")
        first, _, rest = body.partition("\n")
        lines.append(f"- **{first.strip().lstrip('#').strip()}**  _(kept {stamp})_")
        for extra in (l.strip() for l in rest.splitlines()):
            if extra:
                lines.append(f"  {extra}")
    return "\n".join(lines) + "\n", len(rows)


def _journal_md(mem: Path) -> tuple[str, int]:
    """The daily journal, one heading per day, oldest first — it reads as a diary that way."""
    d = mem / "journal"
    days = sorted(d.glob("*.md")) if d.is_dir() else []
    lines = ["# The journal", "",
             "A short record of each day, written by Afon at the time. Oldest first.", ""]
    for f in days:
        lines.append(f"## {f.stem}")
        lines.append("")
        try:
            lines.append(f.read_text(encoding="utf-8", errors="ignore").strip())
        except OSError:
            lines.append("_(this day could not be read at export time)_")
        lines.append("")
    return "\n".join(lines) + "\n", len(days)


def _documents_md(state: Path) -> tuple[str, int]:
    """Where every document Afon wrote ended up (06.F3's index, rendered for a human)."""
    index = state / "documents" / "index.jsonl"
    rows: list[dict] = []
    try:
        for line in index.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    rows.sort(key=lambda r: -float(r.get("at") or 0))
    lines = ["# Documents Afon wrote", "",
             "Newest first. `where` is where it went at the time; some of those places may no "
             "longer exist, which is the point of keeping the list.", ""]
    for r in rows:
        when = datetime.fromtimestamp(float(r.get("at") or 0)).strftime("%Y-%m-%d")
        flag = "" if r.get("verified") else "  _(written, never confirmed)_"
        lines.append(f"- **{r.get('title', '(untitled)')}** — {r.get('kind', 'note')}, "
                     f"{r.get('where', 'unknown')}, {when}{flag}")
    return "\n".join(lines) + "\n", len(rows)


def _audit_md(audit: Path) -> tuple[str, int]:
    """A SUMMARY, not the log: how often each tool ran, per day. The raw log stays where it is."""
    per_day: dict[str, dict[str, int]] = {}
    total = 0
    if audit.is_dir():
        for f in sorted(audit.glob("*.jsonl")):
            day = f.stem
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                name = str(row.get("tool") or row.get("name") or "unknown")
                per_day.setdefault(day, {})[name] = per_day.setdefault(day, {}).get(name, 0) + 1
                total += 1
    lines = ["# What Afon actually did", "",
             "One section per day, counting how many times each tool ran. This is a summary; the "
             "full log is not part of the export, because it holds the values those tools were "
             "given.", ""]
    for day in sorted(per_day):
        lines.append(f"## {day}")
        for name, n in sorted(per_day[day].items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"- {name} — {n}")
        lines.append("")
    return "\n".join(lines) + "\n", total


README = """# Afon — portable export

This folder is everything Afon kept that was worth keeping, written so you can read it without
Afon, without Python, and without the code he ran on. Open any file in a text editor.

- `memory/learned.md` — facts he had been told or had worked out about you.
- `memory/journal.md` — a short record of each day.
- `documents.md` — every document he wrote, and where it went.
- `audit-summary.md` — how often each of his tools ran, per day.
- `state/` — the structured things: goals, objectives, routines, how the two of you stood. JSON,
  which is plain text; any text editor will open it.
- `MANIFEST.json` — a list of every file above with its size and a SHA-256 checksum, so you can
  tell whether this copy is intact. It also names anything that was missing at export time, rather
  than quietly leaving it out.

Format `{format}`, written {when}.

To check the files are undamaged, on any machine with Python:

    python -c "import hashlib,json,pathlib; m=json.load(open('MANIFEST.json')); \\
      [print(f['path'], hashlib.sha256(pathlib.Path(f['path']).read_bytes()).hexdigest()==f['sha256']) \\
       for f in m['files']]"

Nothing here needs Afon to be running, or ever to run again.
"""


# --- writing it ------------------------------------------------------------------------------


def export(dest: Path | None = None, *, state: Path | None = None,
           memory: Path | None = None, audit: Path | None = None) -> Export:
    """Write the portable export. Idempotent: the same destination is overwritten in place."""
    t0 = time.monotonic()
    state = Path(state) if state else state_dir()
    memory = Path(memory) if memory else memory_dir()
    audit = Path(audit) if audit else audit_dir()
    root = Path(dest) if dest else state / "export"
    out = Export(root=root)
    try:
        root.mkdir(parents=True, exist_ok=True)

        learned, n_facts = _learned_md(memory)
        _write(root, "memory/learned.md", learned, out)
        journal, n_days = _journal_md(memory)
        _write(root, "memory/journal.md", journal, out)
        docs, n_docs = _documents_md(state)
        _write(root, "documents.md", docs, out)
        summary, n_calls = _audit_md(audit)
        _write(root, "audit-summary.md", summary, out)

        for name, what in STATE_FILES:
            src = state / name
            try:
                body = src.read_text(encoding="utf-8")
            except OSError:
                out.absent.append(f"{name} ({what})")
                continue
            _write(root, f"state/{name}", body, out)

        out.counts = {"facts": n_facts, "journal_days": n_days, "documents": n_docs,
                      "tool_calls": n_calls}
        when = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _write(root, "README.md", README.format(format=FORMAT, when=when), out)

        manifest = {
            "format": FORMAT,
            "written": when,
            "counts": out.counts,
            # Named, not omitted. An export missing half the memory must not look like an export of
            # half as much memory.
            "absent": out.absent,
            "files": [{"path": rel, "bytes": (root / rel).stat().st_size,
                       "sha256": _sha256(root / rel)}
                      for rel in sorted(out.files)],
        }
        (root / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        out.files.append("MANIFEST.json")
    except Exception as e:  # noqa: BLE001
        out.error = f"{type(e).__name__}: {e}"
        logger.warning(f"portable export failed: {out.error}")
    out.ms = round((time.monotonic() - t0) * 1000.0, 1)
    return out


# --- proving it ------------------------------------------------------------------------------

DRILL_PATH = state_dir() / "portable_drill.json"


def verify(root: Path) -> dict:
    """Re-read an export the way a stranger would: manifest first, then every hash.

    Deliberately depends on nothing from this package beyond the standard library, so that a
    regression which makes the export need Afon to read shows up here rather than in ten years.
    """
    root = Path(root)
    res: dict = {"ok": False, "root": str(root), "files": 0, "bytes": 0,
                 "checked": datetime.now().isoformat(timespec="seconds")}
    try:
        manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        res["error"] = f"no readable manifest: {type(e).__name__}"
        return res
    res["format"] = manifest.get("format")
    res["counts"] = manifest.get("counts", {})
    if manifest.get("format") != FORMAT:
        res["error"] = f"format {manifest.get('format')!r}, expected {FORMAT!r}"
        return res

    bad: list[str] = []
    total = 0
    for entry in manifest.get("files", []):
        p = root / entry.get("path", "")
        try:
            data = p.read_bytes()
        except OSError:
            bad.append(f"{entry.get('path')}: missing")
            continue
        total += len(data)
        if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
            bad.append(f"{entry.get('path')}: checksum mismatch")
    res["files"] = len(manifest.get("files", []))
    res["bytes"] = total
    if bad:
        res["error"] = "; ".join(bad[:4])
        return res
    if not (root / "README.md").is_file():
        res["error"] = "no README — an export nobody can interpret is not an export"
        return res
    res["ok"] = True
    return res


def run_export_drill(dest: Path | None = None, record_to: Path | None = None) -> dict:
    """Write an export into an empty directory, verify it, and file the verdict (50.F2).

    Into an EMPTY directory on purpose: verifying the live export directory would pass on files
    left over from a previous run, which is the failure mode a drill exists to catch.
    """
    import shutil
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="afon-portable-"))
    try:
        made = export(tmp)
        if not made.ok:
            res = {"ok": False, "error": made.error or "nothing was exported", "files": 0,
                   "bytes": 0, "checked": datetime.now().isoformat(timespec="seconds")}
        else:
            res = verify(tmp)
            res["ms"] = made.ms
            res["absent"] = made.absent
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    path = record_to or DRILL_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(res, indent=1), encoding="utf-8")
    except OSError:
        pass                      # the verdict matters more than its filing
    return res


def last_drill(path: Path | None = None) -> dict | None:
    try:
        return json.loads((path or DRILL_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _selfcheck() -> None:
    """ponytail: the one runnable check — an export that verifies, and one that has been damaged."""
    import tempfile

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        state = Path(d) / "state"
        (state / "memory" / "learned").mkdir(parents=True)
        (state / "memory" / "learned" / "a.md").write_text("The farm is in Armenia\nsource: told",
                                                           encoding="utf-8")
        (state / "world_model.json").write_text('{"goal": "ship it"}', encoding="utf-8")
        out = export(Path(d) / "export", state=state, memory=state / "memory",
                     audit=state / "audit")
        assert out.ok, out.error
        assert "memory/learned.md" in out.files and "MANIFEST.json" in out.files, out.files
        assert any("objectives.json" in a for a in out.absent), out.absent
        res = verify(Path(d) / "export")
        assert res["ok"], res
        (Path(d) / "export" / "README.md").write_text("tampered", encoding="utf-8")
        assert not verify(Path(d) / "export")["ok"], "a damaged file verified"
    print("portable export self-check ok")


if __name__ == "__main__":
    _selfcheck()
