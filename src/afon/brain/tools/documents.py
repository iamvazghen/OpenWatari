"""Document tools — "read this doc and answer" (Phase 4.4).

Three read-only tools over a temporary one-document index (`brain/docstore.py`): open a local file,
ask grounded questions about it, and close it. The model summarises/answers from the returned text,
so answers stay grounded in the document instead of the model's memory.

**"Local" means the owner's laptop, not the brain host.** The brain runs on a VPS, so resolving the
path there meant every real path he could name came back "I can't find a file at …". The file is now
READ on the laptop and PARSED here: the PDF reader is installed on the brain and not on the laptop,
so shipping the whole load over would have swapped a missing-file bug for a broken-PDF one. Only the
bytes cross; the index stays brain-side, so `ask_document` needs no further round trips.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from afon.brain import docstore
from afon.brain.tools.base import tool_error

#: Ceiling on a document fetched from the laptop. The brain's WS server accepts 4MB frames
#: (server.py) and base64 inflates by 4/3, so anything past ~2.9MB raw would be dropped by the
#: transport as an oversized frame — an unexplained disconnect instead of an honest refusal. This is
#: tighter than docstore's own 5MB file limit, which applies to files already on this host.
MAX_DOC_BYTES = 2_800_000


async def _pc_doc_read(args: dict) -> str:
    """Read a document off THIS machine's disk and hand back its bytes. Runs ON THE LAPTOP."""
    raw = (args.get("path") or "").strip()
    if not raw:
        return json.dumps({"ok": False, "out": "no path given"})
    p = Path(raw).expanduser()
    if not p.is_file():
        return json.dumps({"ok": False, "out": f"I can't find a file at '{raw}', sir."})
    try:
        size = p.stat().st_size
    except OSError as e:
        return json.dumps({"ok": False, "out": f"I couldn't read '{p.name}', sir: {type(e).__name__}."})
    if size > MAX_DOC_BYTES:
        return json.dumps({"ok": False, "out": (
            f"'{p.name}' is {size / 1_000_000:.1f} MB, sir — too large for me to pull across from "
            "your laptop in one piece.")})
    try:
        return json.dumps({"ok": True, "name": p.name,
                           "b64": base64.b64encode(p.read_bytes()).decode("ascii")})
    except OSError as e:
        return json.dumps({"ok": False, "out": f"I couldn't read '{p.name}', sir: {type(e).__name__}."})


async def read_document(args: dict) -> str:
    path = (args.get("path") or "").strip()
    if not path:
        return "Which file should I read, sir? Give me its path."

    from afon.brain.tools.system import _dispatch

    try:
        raw = await _dispatch("doc_read", {"path": path}, _pc_doc_read, timeout=120.0)
    except Exception as e:  # noqa: BLE001 — laptop dropped mid-read
        return f"Your laptop didn't answer, sir ({type(e).__name__}) — it may be offline."
    try:
        got = json.loads(raw)
    except (TypeError, ValueError):
        # _dispatch turns a dropped link into a spoken sentence rather than raising, so a non-JSON
        # reply is usually that message. Speak it as-is; parsing it and reporting "I couldn't make
        # sense of that" would bury the one explanation the owner actually needs.
        return str(raw)
    if not got.get("ok"):
        return str(got.get("out") or f"I couldn't read '{path}', sir.")

    try:
        data = base64.b64decode(got.get("b64") or "")
    except Exception:  # noqa: BLE001
        return "That document didn't arrive intact, sir — try again."
    ok, msg = docstore.STORE.load_bytes(str(got.get("name") or Path(path).name), data)
    if ok:
        msg += (" Ask me anything about it with 'ask the document …', or say 'close the document' "
                "when you're done.")
    return msg


async def ask_document(args: dict) -> str:
    if not docstore.STORE.loaded():
        return "No document is open, sir — point me at one first with 'read this file …'."
    query = (args.get("query") or "").strip()
    if not query:
        return "What would you like to know from the document, sir?"
    # ask() can reach the optional L5 embedder, which is a network/model call — a failure there must
    # degrade in prose, not surface as an exception name via the agent's blanket catch.
    try:
        hits = docstore.STORE.ask(query)
    except Exception as e:  # noqa: BLE001
        return tool_error("document search", e)
    if not hits:
        return (f"I couldn't find anything about that in '{docstore.STORE.source}', sir.")
    joined = "\n---\n".join(hits)
    return (f"From '{docstore.STORE.source}', the most relevant parts, sir:\n{joined}")


async def close_document(_args: dict) -> str:
    try:
        return docstore.STORE.clear()
    except Exception as e:  # noqa: BLE001
        return tool_error("document close", e)


async def create_document(args: dict) -> str:
    """06.F1/06.F2 — the one creation path, and it reads the document back before saying done."""
    try:
        from afon.brain.documents import create

        written = await create(
            (args.get("title") or ""), (args.get("body") or ""),
            kind=(args.get("kind") or "note"),
            destination=(args.get("destination") or "vault"),
            parent_id=(args.get("parent_id") or ""),
        )
        return written.spoken()
    except Exception as e:  # noqa: BLE001
        return tool_error("document create", e)


async def find_document(args: dict) -> str:
    """06.F3 — "the note you wrote yesterday about X" resolves to a document he can open."""
    try:
        from afon.brain.documents import find

        rows = find(args.get("query") or "", kind=(args.get("kind") or ""),
                    destination=(args.get("destination") or ""))
        if not rows:
            return ("I haven't written anything matching that, sir. Note that this is what I have "
                    "written myself — search_vault covers the whole vault.")
        lines = []
        for r in rows:
            when = _ago(r.get("at", 0.0))
            flag = "" if r.get("verified") else " (written, but I couldn't confirm it)"
            lines.append(f"{r.get('kind', 'note')} '{r.get('title')}' — {r.get('where')}, "
                         f"{when}{flag}  [{r.get('ref')}]")
        return "Documents I've written, sir:\n" + "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        return tool_error("document lookup", e)


def _ago(at: float) -> str:
    import time

    s = max(0.0, time.time() - float(at or 0))
    if s < 3600:
        return "just now" if s < 300 else f"{int(s // 60)} minutes ago"
    if s < 86400:
        return f"{int(s // 3600)} hours ago"
    days = int(s // 86400)
    return "yesterday" if days == 1 else f"{days} days ago"


SCHEMAS = [
    {"type": "function", "function": {
        "name": "create_document",
        "description": "Write a real document and put it where it belongs — the ONLY way to create "
                       "one. Destinations: 'vault' (an Obsidian note, the default), 'local' (a "
                       "markdown file on this host), 'notion' (a sub-page; needs parent_id). Use "
                       "for 'write that up', 'make a note of this', 'draft a decision record', "
                       "'put that in my vault'. The document is read back before success is "
                       "reported, so a reply that says 'written and checked' means it is there.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "What the document is called."},
            "body": {"type": "string", "description": "The full text, in Markdown."},
            "kind": {"type": "string", "enum": ["note", "meeting", "decision", "brief", "review"],
                     "description": "What kind of document it is. Default 'note'."},
            "destination": {"type": "string", "enum": ["vault", "local", "notion"],
                            "description": "Where it goes. Default 'vault'."},
            "parent_id": {"type": "string",
                          "description": "Notion parent page id; only for destination 'notion'."}},
            "required": ["title", "body"]}}},
    {"type": "function", "function": {
        "name": "find_document",
        "description": "Find a document YOU wrote earlier — 'the note you wrote yesterday about the "
                       "farm', 'that decision record', 'what have you written lately'. Returns each "
                       "one's title, where it went, when, and the reference needed to open it. For "
                       "notes the owner wrote himself, use search_vault instead.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Words from the title. Omit for the newest."},
            "kind": {"type": "string", "description": "Optional: only this kind."},
            "destination": {"type": "string", "description": "Optional: only this destination."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "read_document",
        "description": "Open a LOCAL document (text, Markdown, code, CSV, JSON; PDF if a reader is "
                       "installed) into a temporary index so you can answer questions grounded in it. "
                       "Use for 'read this file and summarise it', 'what does this doc say about X'.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Local file path to read."}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "ask_document",
        "description": "Ask a question about the currently-open document; returns the most relevant "
                       "passages to answer from. Read the document first with read_document.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "The question to answer from the document."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "close_document",
        "description": "Close the currently open document and drop its temporary index. Use for "
                       "'close that document', 'I'm done with the PDF'. read_document opens one; "
                       "ask_document queries the open one.",
        "parameters": {"type": "object", "properties": {}, "required": []}}},
]

HANDLERS = {
    "create_document": create_document,
    "find_document": find_document,
    "read_document": read_document,
    "ask_document": ask_document,
    "close_document": close_document,
}

# The laptop executor reads the file; the brain parses and indexes it. Only doc_read crosses — asking
# and closing operate on the in-memory index, which lives brain-side.
LOCAL_HANDLERS = {"doc_read": _pc_doc_read}
