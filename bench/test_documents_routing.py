"""H1.2 — "read this file" means a file on the OWNER's laptop, not on the brain host.

The brain runs on a VPS, so `docstore.load(path)` resolved every path against the server: any real
path the owner could name came back "I can't find a file at …". The fix reads the file on the laptop
and parses it here, because the PDF reader is installed on the brain and NOT on the laptop — moving
the whole load across would have swapped a missing-file bug for a broken-PDF one.

Covers: read_document dispatches; the split really does keep PDF parsing brain-side; the transport
ceiling matches what the brain's WS server accepts; failures degrade in prose; ask/close stay local
to the index; and pc_agent wires the op up.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f" — {detail}" if detail and not ok else ""))


def _tmpfile(data: bytes, suffix: str) -> Path:
    fd, name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)  # mkstemp hands back an OPEN fd; on Windows that blocks the later unlink
    p = Path(name)
    p.write_bytes(data)
    return p


def main() -> None:
    import afon.brain.tools.documents as docs
    from afon.brain import docstore
    from afon.brain.pc_link import PC_LINK

    print("[1] read_document goes to the laptop instead of the brain's filesystem")
    seen: list[tuple[str, dict]] = []
    body = b"# Roadmap\n\nThe budget ceiling is 40000 euros.\n\nDelivery slips to March.\n"
    src = _tmpfile(body, ".md")

    async def fake_forward(op, args, timeout=None):
        seen.append((op, args))
        return json.dumps({"ok": True, "name": "roadmap.md",
                           "b64": base64.b64encode(body).decode("ascii")})

    real_active = type(PC_LINK).active
    type(PC_LINK).active = property(lambda self: True)
    PC_LINK.forward = fake_forward  # type: ignore[method-assign]
    try:
        msg = asyncio.run(docs.read_document({"path": "D:/notes/roadmap.md"}))
        check("dispatches rather than resolving locally", bool(seen), str(msg)[:80])
        check("...as the doc_read op", seen and seen[0][0] == "doc_read", str(seen[:1]))
        check("...forwarding the owner's path verbatim",
              seen and seen[0][1].get("path") == "D:/notes/roadmap.md", str(seen[:1]))
        check("the returned bytes are indexed here", "roadmap.md" in msg, msg[:90])
        check("...and it invites the follow-up", "ask the document" in msg, msg[:90])

        print("\n[2] the index lives brain-side — asking needs no further round trip")
        before = len(seen)
        ans = asyncio.run(docs.ask_document({"query": "budget ceiling"}))
        check("ask_document answers from the loaded doc", "40000" in ans, ans[:90])
        check("...without another laptop hop", len(seen) == before, str(len(seen) - before))
        closed = asyncio.run(docs.close_document({}))
        check("close_document clears it", not docstore.STORE.loaded(), closed[:60])

        print("\n[3] a laptop-side failure is spoken, not swallowed")

        async def fail_forward(op, args, timeout=None):
            return json.dumps({"ok": False, "out": "I can't find a file at 'D:/nope.md', sir."})

        PC_LINK.forward = fail_forward  # type: ignore[method-assign]
        miss = asyncio.run(docs.read_document({"path": "D:/nope.md"}))
        check("missing file reported in the laptop's words", "can't find" in miss, miss[:90])

        async def boom_forward(op, args, timeout=None):
            raise ConnectionError("laptop not connected")

        PC_LINK.forward = boom_forward  # type: ignore[method-assign]
        dead = asyncio.run(docs.read_document({"path": "D:/x.md"}))
        # _dispatch catches the drop and returns its own spoken sentence rather than raising, so the
        # owner must hear THAT — not a parse complaint about it, which is what this used to do.
        check("dead laptop degrades in prose", "didn't respond" in dead, dead[:90])

        async def junk_forward(op, args, timeout=None):
            return "not json at all"

        PC_LINK.forward = junk_forward  # type: ignore[method-assign]
        junk = asyncio.run(docs.read_document({"path": "D:/x.md"}))
        check("a non-JSON reply is spoken, not swallowed", "not json at all" in junk, junk[:90])
    finally:
        type(PC_LINK).active = real_active

    check("no path still asks which file", "Which file" in asyncio.run(docs.read_document({})))

    print("\n[4] the executor reads real files and enforces the transport ceiling")
    got = json.loads(asyncio.run(docs._pc_doc_read({"path": str(src)})))
    check("executor returns the bytes", got["ok"] and base64.b64decode(got["b64"]) == body, str(got)[:70])
    check("...and the real filename", got["name"] == src.name, str(got.get("name")))
    gone = json.loads(asyncio.run(docs._pc_doc_read({"path": "D:/definitely/not/here.md"})))
    check("missing file refused at the executor", gone["ok"] is False, str(gone))
    src.unlink(missing_ok=True)

    big = _tmpfile(b"x" * (docs.MAX_DOC_BYTES + 1), ".txt")
    over = json.loads(asyncio.run(docs._pc_doc_read({"path": str(big)})))
    check("oversize refused before it hits the wire", over["ok"] is False, str(over)[:70])
    check("...saying so in plain words", "too large" in str(over.get("out", "")), str(over)[:70])
    # The brain's WS server caps inbound frames at 4MB and base64 inflates by 4/3; a cap above that
    # would surface as an unexplained dropped connection instead of the honest refusal above.
    check("ceiling fits the 4MB inbound frame", docs.MAX_DOC_BYTES * 4 / 3 < 4 * 1024 * 1024,
          str(docs.MAX_DOC_BYTES))
    big.unlink(missing_ok=True)

    print("\n[5] parsing stayed on the brain — that's why PDFs still work")
    # pypdf is installed on the VPS brain and not on the laptop, so load_bytes must do the extraction.
    check("load_bytes ingests text from raw bytes",
          docstore.STORE.load_bytes("notes.md", b"# T\n\nalpha beta\n")[0])
    check("...and rejects an unreadable type",
          docstore.STORE.load_bytes("thing.exe", b"MZ")[0] is False)
    # This suite runs on the LAPTOP, where pypdf isn't installed — so it can't prove extraction
    # succeeds, only that .pdf bytes reach the extractor instead of being rejected by file type.
    # That's the property that matters: parsing is attempted wherever load_bytes runs (the brain,
    # in production, which is the host that has pypdf).
    _, pdf_msg = docstore.STORE.load_bytes("broken.pdf", b"%PDF-1.4 not really a pdf")
    check("a .pdf reaches the PDF extractor, not the 'unknown type' branch",
          "don't know how to read" not in pdf_msg, pdf_msg[:90])
    docstore.STORE.clear()

    print("\n[6] pc_agent wires doc_read up")
    import afon.edge.pc_agent as agent
    check("pc_agent executes 'doc_read'", "doc_read" in agent.LOCAL_HANDLERS,
          str(sorted(agent.LOCAL_HANDLERS)))
    check("executor is not the tool entry point (no brain->laptop->brain loop)",
          docs.LOCAL_HANDLERS["doc_read"] is not docs.HANDLERS["read_document"])

    print("\n[7] 06.F3 — a document Afon wrote is addressable afterwards")
    # "The note you wrote yesterday about the farm" is not a vault search: search_vault covers
    # everything the OWNER wrote too, and it has no idea which notes were Afon's or when. Before
    # this, nothing recorded what he had created, so a document he had just written became
    # anonymous the moment the turn ended — and for Notion it was worse than anonymous, because
    # the create call threw the page id away and there was no way back to the page at all.
    from afon.brain import documents as D
    from afon.brain.tools.documents import find_document
    from afon.config import settings

    real_state = settings.state_dir
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as root:
        try:
            settings.state_dir = root
            check("nothing written yet resolves to nothing", D.find("farm") == [])
            body = "Forty sacks of feed, delivered Thursday to the lower barn."
            asyncio.run(D.create("Rabbit farm feed order", body, kind="note", destination="local"))
            asyncio.run(D.create("German lesson plan", body, kind="brief", destination="local"))

            hit = D.find("the note you wrote yesterday about the farm")
            check("a spoken description resolves to the right document",
                  [r["title"] for r in hit] == ["Rabbit farm feed order"], hit)
            check("...carrying the reference needed to open it",
                  Path(hit[0]["ref"]).is_file(), hit[0]["ref"])
            check("...and when it was written", hit[0]["at"] > 0)
            check("an unrelated document is not dragged in", len(hit) == 1, hit)
            check("asking by kind narrows it",
                  [r["title"] for r in D.find("", kind="brief")] == ["German lesson plan"],
                  D.find("", kind="brief"))
            check("asking for nothing in particular gives the newest first",
                  [r["title"] for r in D.find("")][0] == "German lesson plan", D.find(""))
            check("a query matching nothing says nothing, rather than guessing",
                  D.find("submarine") == [])

            said = asyncio.run(find_document({"query": "farm"}))
            check("the tool answers in prose with the location", "Rabbit farm feed order" in said
                  and "documents folder" in said, said)
            check("...and points at search_vault for notes the owner wrote himself",
                  "search_vault" in asyncio.run(find_document({"query": "submarine"})),
                  asyncio.run(find_document({"query": "submarine"})))

            # A write that could not be confirmed must still be findable — that is precisely the
            # document the owner needs to go and look at.
            D.record(D.Written("notion", "Unconfirmed brief", where="Notion", ref="p-9",
                               why="couldn't read it back"))
            rows = D.find("unconfirmed")
            check("a document written but unverified is still addressable", len(rows) == 1, rows)
            check("...and is flagged as unconfirmed when named",
                  "couldn't confirm" in asyncio.run(find_document({"query": "unconfirmed"})),
                  asyncio.run(find_document({"query": "unconfirmed"})))

            # A half-written last line (a crash mid-append) must not lose the whole index.
            with (Path(root) / "documents" / "index.jsonl").open("a", encoding="utf-8") as fh:
                fh.write('{"title": "torn')
            check("a torn index line does not lose the rest", len(D.created()) == 3, D.created())
        finally:
            settings.state_dir = real_state

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
