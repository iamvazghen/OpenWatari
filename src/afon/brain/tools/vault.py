"""Obsidian vault tools — READ/SEARCH the canonical knowledge base.

Reads the LOCAL mirror at ``settings.vault_path`` (the VPS-authoritative one-way sync target,
e.g. ``C:\\Users\\iamva\\Documents\\Obsidian Vault``). Writes are deliberately NOT offered:
the vault is a one-way VPS->local sync that nukes-and-replaces each local dir, so any local
write would be clobbered. When Afon needs to change the vault he delegates that to the
fleet (ispir), which writes on the VPS.

Pure local filesystem — no credentials, works fully offline. Search is a lightweight
filename+content scorer (good enough for "find the rabbit-farm charter" voice queries).
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path

from afon.brain.tools.base import clip, not_configured, tool_error
from afon.config import settings

# Filenames/folders are sanitised to a safe set so a voice-dictated note title can't escape the
# vault or create odd paths.
_SAFE_NAME = re.compile(r"[^A-Za-z0-9 _.\-]+")


def _vault_root() -> Path | None:
    if not settings.vault_path:
        return None
    p = Path(settings.vault_path)
    return p if p.is_dir() else None


def _score(query: str, name: str, body: str) -> int:
    q = query.lower()
    terms = [t for t in q.replace("-", " ").split() if t]
    score = 0
    nlow = name.lower()
    if q in nlow:
        score += 50
    # Lowercase ONCE, not once per term. This was inside the loop, so a 4-term query built four
    # full lowercase copies of every note in a 25MB vault — the single largest cost in a search
    # once the body cache removed the reads.
    blow = body.lower()
    for t in terms:
        if t in nlow:
            score += 10
        score += blow.count(t)
    return score


def _snippet(body: str, query: str, width: int = 200) -> str:
    low = body.lower()
    terms = [t for t in query.lower().replace("-", " ").split() if t]
    idx = next((low.find(t) for t in terms if low.find(t) >= 0), -1)
    if idx < 0:
        return clip(body, width)
    start = max(0, idx - width // 3)
    return clip(body[start : start + width], width)


# Body cache, keyed by path -> (mtime, size, body). The vault is 10,716 files / 26.0MB: the
# rglob costs 400ms and the stats another 250ms, but READING all of it costs **73s** on a cold
# OS file cache, every single search. Re-reading a note whose mtime and size are unchanged cannot
# return different text, so the reads are pure waste after the first pass.
#
# (The 4.9s this comment used to claim was measured against an already-warm OS cache and was
# wildly optimistic. Measured 2026-08-09: first full scan 108s, second 3.2s, third 2.4s.)
#
# ponytail: a dict keyed on (mtime, size), not an index. An index means a schema, a writer, an
# invalidation story and a rebuild command; this is eight lines and gets the same 10x. Build the
# index if the vault outgrows _CACHE_BUDGET_BYTES enough to matter (TODO J3.3a).
_CACHE: dict[Path, tuple[float, int, str]] = {}
# Bounded so a growing vault can never quietly become a memory leak in a long-lived brain
# process. Past the budget, files are still searched — just read fresh each time, i.e. exactly
# the old behaviour. Degrading to "slow" rather than "wrong" is the point.
_CACHE_BUDGET_BYTES = 32 * 1024 * 1024
_cached_bytes = 0


def _body(path: Path) -> str | None:
    """The note's text, from cache when the file is provably unchanged. None if unreadable."""
    global _cached_bytes
    try:
        st = path.stat()
    except OSError:
        return None
    hit = _CACHE.get(path)
    if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
        return hit[2]
    try:
        body = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if _cached_bytes + st.st_size <= _CACHE_BUDGET_BYTES:
        if hit:
            _cached_bytes -= hit[1]
        _CACHE[path] = (st.st_mtime, st.st_size, body)
        _cached_bytes += st.st_size
    return body


def _search_sync(query: str, root: Path) -> str:
    """The actual scan. Synchronous and CPU/IO-bound — callers run it off the event loop."""
    hits: list[tuple[int, Path, str]] = []
    for p in root.rglob("*.md"):
        body = _body(p)
        if body is None:
            continue
        s = _score(query, p.stem, body)
        if s > 0:
            hits.append((s, p, body))
    hits.sort(key=lambda h: h[0], reverse=True)
    if not hits:
        return f"Nothing in the vault matched '{query}', sir."
    top = hits[: settings.vault_search_max_results]
    lines = []
    for _, p, body in top:
        rel = p.relative_to(root).as_posix()
        lines.append(f"[{rel}] {_snippet(body, query)}")
    return f"Found {len(hits)} vault note(s) matching '{query}'. Top results:\n" + "\n".join(lines)


def warm_cache() -> int:
    """Read every note once so the body cache is warm. Returns notes cached. Never raises.

    Without this the L3 leg of `fused_recall` is not slow — it is UNREACHABLE. A cold scan of this
    vault costs ~108s against a 6s budget, so `fused_recall` abandons the wait every time; and
    because the module cache dies with the process, every brain restart puts it back to cold. The
    warm scan is ~3s, comfortably inside the budget. Measured 2026-08-09: 108s / 3.2s / 2.4s.

    Cheap to call again — an unchanged note is a stat, not a read.
    """
    root = _vault_root()
    if root is None:
        return 0
    n = 0
    for p in root.rglob("*.md"):
        if _body(p) is not None:
            n += 1
    return n


async def search_vault(args: dict) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return "I need something to search the vault for, sir."
    root = _vault_root()
    if root is None:
        return not_configured("the Obsidian vault", "AFON_VAULT_PATH set to the vault folder")
    try:
        # THREADED HERE, not at the call site. This function was `async def` wrapping a fully
        # synchronous body, so awaiting it held the event loop for the whole scan — measured at
        # 287s on a cold vault, during which the brain answers nothing at all. `fused_recall`
        # worked around it locally; every other caller (the LLM calling this as a tool) still
        # froze the loop. Fixing it in the one place fixes it for all of them.
        return await asyncio.to_thread(_search_sync, query, root)
    except Exception as e:  # noqa: BLE001
        return tool_error("vault search", e)


async def read_vault_note(args: dict) -> str:
    rel = (args.get("path") or "").strip()
    if not rel:
        return "Which note should I read, sir? Give me a path or search first."
    root = _vault_root()
    if root is None:
        return not_configured("the Obsidian vault", "AFON_VAULT_PATH set to the vault folder")
    try:
        target = (root / rel).resolve()
        # Stay inside the vault — no path traversal.
        if root.resolve() not in target.parents and target != root.resolve():
            return "That path is outside the vault, sir — I won't read it."
        if not target.is_file():
            return f"I couldn't find a note at '{rel}', sir."
        body = target.read_text(encoding="utf-8", errors="ignore")
        return f"{rel}:\n{clip(body, settings.vault_read_max_chars)}"
    except Exception as e:  # noqa: BLE001
        return tool_error("vault read", e)


async def write_vault(args: dict) -> str:
    """Save a note INTO the vault (append by default, or create). Only on the authoritative host.

    This is how Afon keeps the knowledge base alive himself — capturing a decision, a fact worth
    keeping, or a session note. It's a no-op with a spoken explanation when the host isn't the vault
    owner (AFON_VAULT_WRITABLE), so it can never clobber the laptop's one-way-synced mirror.
    """
    if not settings.vault_writable:
        return ("Vault writing is off here, sir — it's only enabled on the host that owns the "
                "vault. Set AFON_VAULT_WRITABLE=true there and I can save notes into it.")
    root = _vault_root()
    if root is None:
        return not_configured("the Obsidian vault", "AFON_VAULT_PATH set to the vault folder")
    content = (args.get("content") or "").strip()
    if not content:
        return "There's nothing to write, sir — give me the note content."
    note = (args.get("note") or args.get("title") or "").strip()
    folder = _SAFE_NAME.sub("", (args.get("folder") or "Afon").replace("/", " ")).strip() or "Afon"
    mode = (args.get("mode") or "append").strip().lower()
    stem = _SAFE_NAME.sub("", note).strip() or datetime.now().strftime("%Y-%m-%d note")
    if not stem.lower().endswith(".md"):
        stem += ".md"
    try:
        rootr = root.resolve()
        target = (rootr / folder / stem).resolve()
        # Stay inside the vault — no path traversal out of it.
        if rootr != target and rootr not in target.parents:
            return "That path is outside the vault, sir — I won't write it."
        target.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        if mode == "create" or not target.exists():
            target.write_text(f"# {note or stem[:-3]}\n\n{content}\n", encoding="utf-8")
            action = "created"
        else:
            with target.open("a", encoding="utf-8") as f:
                f.write(f"\n\n## {stamp}\n{content}\n")
            action = "added to"
        return f"Done, sir — {action} the vault note {target.relative_to(rootr).as_posix()}."
    except Exception as e:  # noqa: BLE001
        return tool_error("vault write", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_vault",
            "description": (
                "Search the owner's Obsidian knowledge vault (his notes, project charters, "
                "agent docs, decisions) for a topic and get the top matching notes with "
                "snippets. Use for 'search my vault/notes for X' or to ground an answer in "
                "his own documents."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search the vault for."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_vault_note",
            "description": (
                "Read the full text of a specific vault note by its relative path (as returned "
                "by search_vault, e.g. '30-Projects/lpstrak-rabbit-farm/CHARTER.md')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Vault-relative path to the note."}
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_vault",
            "description": (
                "Save a note INTO the owner's Obsidian vault — append to (default) or create a note. "
                "Use to record a decision, a durable fact, or a session summary worth keeping in "
                "the knowledge base. Only works on the host that owns the vault."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string",
                             "description": "Note title / filename (without .md)."},
                    "content": {"type": "string", "description": "The text to write."},
                    "mode": {"type": "string", "enum": ["append", "create"],
                             "description": "append (default) or create a fresh note."},
                    "folder": {"type": "string",
                               "description": "Vault subfolder (default 'Afon')."},
                },
                "required": ["content"],
            },
        },
    },
]

HANDLERS = {"search_vault": search_vault, "read_vault_note": read_vault_note,
            "write_vault": write_vault}
