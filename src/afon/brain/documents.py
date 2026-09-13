"""06.F1-F3 — one creation path, verified by reading it back, and addressable afterwards.

There were three writers and no creator. `write_vault` wrote a file and said "Done, sir — created
the vault note X". `notion_create_page` POSTed and said "Created the Notion page 'X', sir" —
throwing away the response, including the page id, so the thing it had just made could never be
referred to again. Nothing wrote local markdown at all. Each reported success on the strength of
not having raised, which is a different claim from "it is there".

That distinction is the whole floor. A write that returns without an exception has been *accepted*;
whether it landed is a question only a read answers. The failures that hide in the gap are not
exotic — a Notion parent id that resolves to an archived page, a vault path on a mount that has
gone read-only since startup, a title that sanitises down to nothing — and every one of them ends
with Afon saying "Done, sir" about a document that does not exist.

So: `create()` writes, reads back, and compares. Success is reported only when the words come back.
When they do not, it says which half happened, because "I wrote it but couldn't confirm it" and
"it failed" send the owner to different places.

The second half is addressability. Each created document is recorded — title, kind, where, and the
reference a later read needs — so "the note you wrote yesterday about the farm" resolves to a
document rather than to a search of the whole vault. Only the TITLE is kept, never the body: the
document already exists in the place it was written, and a second copy in the state root would be
one more thing to keep in step and one more thing to forget on request.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from afon.shared.paths import state_dir

#: Where a document can be put. The names the owner would use, not the module names behind them.
DESTINATIONS = ("vault", "local", "notion")

#: What kind of thing it is. Recorded so it can be asked for by kind; templates are 06.R1.
KINDS = ("note", "meeting", "decision", "brief", "review")

#: How much of the body has to come back before a write counts as verified. Long enough that a
#: truncating or silently-discarding destination fails, short enough to survive a destination that
#: reformats (Notion turns paragraphs into blocks and normalises whitespace).
_FINGERPRINT_CHARS = 120

_WS = re.compile(r"\s+")
_SAFE_NAME = re.compile(r"[^A-Za-z0-9 _.\-]+")


def _norm(text: str) -> str:
    return _WS.sub(" ", (text or "")).strip().lower()


@dataclass(frozen=True)
class Written:
    """What was created, where, and whether it was actually found there afterwards."""

    destination: str
    title: str
    where: str = ""          # said out loud: a vault-relative path, a file name, a page title
    ref: str = ""            # what a later read needs: an absolute path, or a Notion page id
    kind: str = "note"
    at: float = field(default_factory=time.time)
    verified: bool = False
    why: str = ""            # why it is not verified — never empty when `verified` is False

    def spoken(self) -> str:
        if self.verified:
            return f"Written and checked, sir — {self.kind} '{self.title}' is in {self.where}."
        if self.ref:
            return (f"I wrote {self.kind} '{self.title}' to {self.where}, sir, but I couldn't read "
                    f"it back to confirm it: {self.why}. Worth a look before you rely on it.")
        return f"I couldn't write '{self.title}', sir — {self.why}."

    def row(self) -> dict:
        return {"destination": self.destination, "title": self.title, "where": self.where,
                "ref": self.ref, "kind": self.kind, "at": self.at, "verified": self.verified}


# --- the index -----------------------------------------------------------------------------


def _docs_dir() -> Path:
    return state_dir() / "documents"


def _index_path() -> Path:
    return _docs_dir() / "index.jsonl"


def record(written: Written) -> None:
    """Append one line to the index. Fail-quiet: an unwritable index must not lose the document."""
    if not written.ref:
        return
    try:
        _docs_dir().mkdir(parents=True, exist_ok=True)
        with _index_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(written.row(), ensure_ascii=False) + "\n")
    except OSError:
        pass


def created(limit: int = 200) -> list[dict]:
    """The documents Afon has written, newest first."""
    try:
        lines = _index_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue                      # a half-written line is not a reason to lose the rest
    out.reverse()
    return out


def find(query: str = "", *, kind: str = "", destination: str = "", limit: int = 5) -> list[dict]:
    """Resolve "the note you wrote yesterday about X" to the documents that could be it.

    Scored on the title, because that is what gets remembered and said back. An empty query is not
    an error — "what have you written lately" is a real question and the answer is the newest few.
    """
    rows = created()
    if kind:
        rows = [r for r in rows if r.get("kind") == kind]
    if destination:
        rows = [r for r in rows if r.get("destination") == destination]
    words = {w for w in re.findall(r"[a-z0-9]{3,}", (query or "").lower())}
    if not words:
        return rows[:limit]
    scored = []
    for r in rows:
        title = {w for w in re.findall(r"[a-z0-9]{3,}", str(r.get("title", "")).lower())}
        hit = len(words & title)
        if hit:
            scored.append((hit, r.get("at", 0.0), r))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return [r for _, _, r in scored[:limit]]


# --- writing -------------------------------------------------------------------------------


def _write_local(title: str, body: str) -> tuple[str, str]:
    """Markdown under the state root. Returns (ref, where)."""
    stem = _SAFE_NAME.sub("", title).strip() or time.strftime("%Y-%m-%d note")
    path = _docs_dir() / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\n{body}\n", encoding="utf-8")
    return str(path), f"your documents folder as {path.name}"


async def _read_back(destination: str, ref: str) -> str:
    """Fetch what is actually at `ref` now. The only evidence a write landed."""
    if destination == "notion":
        from afon.brain.tools.notion import page_text

        return await page_text(ref)
    return Path(ref).read_text(encoding="utf-8")


async def create(title: str, body: str, *, kind: str = "note", destination: str = "vault",
                 parent_id: str = "") -> Written:
    """Write one document and confirm it is there. Never reports success it has not checked."""
    title = (title or "").strip()
    body = (body or "").strip()
    kind = (kind or "note").strip().lower()
    destination = (destination or "vault").strip().lower()
    if not title:
        return Written(destination, title, why="I need a title for it")
    if not body:
        return Written(destination, title, why="there's nothing to put in it")
    if destination not in DESTINATIONS:
        return Written(destination, title,
                       why=f"I can write to {', '.join(DESTINATIONS)}, not '{destination}'")

    try:
        if destination == "local":
            ref, where = _write_local(title, body)
        elif destination == "vault":
            from afon.brain.tools.vault import write_note

            ref, where = write_note(title, body)
        else:
            from afon.brain.tools.notion import create_page

            ref = await create_page(parent_id, title, body)
            where = "Notion, under the page you named"
    except Exception as e:  # noqa: BLE001 — the destination said no; that is an answer, not a crash
        return Written(destination, title, kind=kind, why=f"{type(e).__name__}: {e}")

    def _out(**kw) -> Written:
        w = Written(destination, title, where=where, ref=ref, kind=kind, **kw)
        record(w)
        return w

    try:
        back = await _read_back(destination, ref)
    except Exception as e:  # noqa: BLE001
        return _out(why=f"reading it back failed ({type(e).__name__})")

    want = _norm(body)[:_FINGERPRINT_CHARS]
    if want and want in _norm(back):
        return _out(verified=True)
    return _out(why="what came back doesn't match what I sent")


def _selfcheck() -> None:
    """ponytail: the one runnable check — a write that lands, and one that silently does not."""
    import asyncio
    import tempfile

    from afon.config import settings

    real = settings.state_dir
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        try:
            settings.state_dir = d
            w = asyncio.run(create("Feed order", "Forty sacks, delivered Thursday.",
                                   kind="note", destination="local"))
            assert w.verified and "Written and checked" in w.spoken(), w
            assert find("feed")[0]["title"] == "Feed order", find("feed")
            assert find("nothing like it") == []
            bad = asyncio.run(create("", "body", destination="local"))
            assert not bad.verified and not bad.ref, bad
        finally:
            settings.state_dir = real
    print("documents self-check ok")


if __name__ == "__main__":
    _selfcheck()
