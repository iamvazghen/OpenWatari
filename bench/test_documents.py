"""Document RAG — Phase 4.4 (hermetic, temp file, no network).

Verifies the temporary one-document index: ingest a local doc, query it (relevant passage ranked
first), close it (index dropped), and graceful handling of missing files, no-doc-open, and an
unreadable type. Reuses the keyword scorer (semantic blend is optional and off here).

    uv run python bench/test_documents.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


DOC = """# Rabbit Farm Notes

The Lpstrak rabbit farm is in Armenia and raises New Zealand White rabbits for meat.

Feeding: rabbits eat timothy hay, leafy greens, and a measured amount of pellets. Fresh water daily.

Breeding: does can be bred at about five months. Gestation is roughly 31 days, with litters of 6 to 10 kits.

Housing: wire hutches off the ground keep the colony clean and reduce disease.
"""


def main() -> None:
    from afon.brain.docstore import DocStore

    tmp = Path(tempfile.mkdtemp(prefix="afon-docs-"))
    doc_path = tmp / "rabbits.md"
    doc_path.write_text(DOC, encoding="utf-8")

    print("[1] ingest a local document")
    store = DocStore()
    ok, msg = store.load(str(doc_path))
    check("load succeeds", ok, msg)
    check("reports the source + a summary head", "rabbits.md" in msg and "Lpstrak" in msg, msg)
    check("store is now loaded with multiple chunks", store.loaded())

    print("\n[2] query returns the most relevant passage")
    hits = store.ask("what do the rabbits eat?")
    check("a relevant passage is returned", bool(hits), str(hits))
    check("feeding passage ranks first", "hay" in hits[0].lower(), hits[0] if hits else "")
    breeding = store.ask("when can does be bred and how long is gestation?")
    check("breeding query finds the breeding passage",
          bool(breeding) and "gestation" in breeding[0].lower(), str(breeding[:1]))
    check("an unrelated query returns nothing", store.ask("quantum chromodynamics") == [])

    print("\n[3] close drops the temporary index")
    out = store.clear()
    check("clear reports the closed doc", "rabbits.md" in out, out)
    check("store is empty after clear", not store.loaded())
    check("querying a closed store returns nothing", store.ask("hay") == [])

    print("\n[4] graceful edges")
    bad_ok, bad_msg = store.load(str(tmp / "missing.md"))
    check("missing file -> clear note, no crash", not bad_ok and "can't find" in bad_msg.lower(), bad_msg)
    weird = tmp / "image.bin"
    weird.write_bytes(b"\x00\x01\x02")
    typ_ok, typ_msg = store.load(str(weird))
    check("unknown type -> clear note", not typ_ok and "don't know how to read" in typ_msg.lower(), typ_msg)

    print("\n[5] tools: registered, lazy-grouped, degrade when no doc open")
    import afon.brain.docstore as ds
    from afon.brain.tools import groups_for_text, tool_names
    from afon.brain.tools.documents import ask_document, read_document

    ds.STORE = DocStore()  # fresh, nothing open
    for name in ("read_document", "ask_document", "close_document"):
        check(f"{name} registered", name in tool_names())
    check("'read this file' activates the docs group", "docs" in groups_for_text("read this file for me"))
    no_doc = asyncio.run(ask_document({"query": "anything"}))
    check("ask with no doc open -> guidance, no crash", "no document is open" in no_doc.lower(), no_doc)
    loaded = asyncio.run(read_document({"path": str(doc_path)}))
    check("read_document tool ingests + invites questions", "Loaded" in loaded and "ask" in loaded.lower(),
          loaded[:80])

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
