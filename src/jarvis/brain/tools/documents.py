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

from jarvis.brain import docstore
from jarvis.brain.tools.base import tool_error

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

    from jarvis.brain.tools.system import _dispatch

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


SCHEMAS = [
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
    "read_document": read_document,
    "ask_document": ask_document,
    "close_document": close_document,
}

# The laptop executor reads the file; the brain parses and indexes it. Only doc_read crosses — asking
# and closing operate on the in-memory index, which lives brain-side.
LOCAL_HANDLERS = {"doc_read": _pc_doc_read}
