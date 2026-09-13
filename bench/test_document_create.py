"""06.F1/06.F2 — one creation path, and nothing is called done until it has been read back.

There were three writers and no creator. `write_vault` wrote a file and said "Done, sir".
`notion_create_page` POSTed and said "Created the Notion page 'X', sir" while discarding the
response — including the page id, so the thing it had just made could never be opened again.
Nothing wrote local markdown at all. Every one of them reported success on the strength of not
having raised, which is a different claim from "it is there".

  [three destinations] vault, local markdown and Notion all go through `create_document`, and the
                       two tools it replaces are no longer offered to the model;
  [read-back]          the document is fetched after writing and compared; a destination that
                       accepts a write and stores nothing is caught, and a write that happened but
                       could not be confirmed is reported as exactly that, not as a failure.

Hermetic: a temporary state root, a temporary vault, and a fake Notion. No network.

    uv run python bench/test_document_create.py
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


BODY = ("The feed order for the rabbit farm goes out on Thursday: forty sacks, delivered to the "
        "lower barn, invoiced at the end of the month.")


def main() -> None:
    from afon.brain import documents as D
    from afon.brain.tools import notion as N
    from afon.config import settings

    real_state, real_vault, real_writable = (settings.state_dir, settings.vault_path,
                                             settings.vault_writable)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        try:
            settings.state_dir = str(Path(d) / "state")
            vault = Path(d) / "vault"
            vault.mkdir()
            settings.vault_path = str(vault)
            settings.vault_writable = True

            print("[three destinations] 06.F1 — one tool owns creation")
            local = asyncio.run(D.create("Feed order", BODY, kind="note", destination="local"))
            check("a local markdown file is written", local.verified, local)
            check("...and really exists", Path(local.ref).is_file(), local.ref)
            check("...with the body in it", BODY in Path(local.ref).read_text(encoding="utf-8"))
            check("...and he says it was checked, not merely written",
                  "Written and checked" in local.spoken(), local.spoken())

            note = asyncio.run(D.create("Decision on feed", BODY, kind="decision",
                                        destination="vault"))
            check("a vault note is written", note.verified, note)
            check("...inside the vault, not beside it",
                  vault.resolve() in Path(note.ref).resolve().parents, note.ref)
            check("...and named by where it landed", "the vault at" in note.where, note.where)

            print("\n[three destinations] Notion goes through the same path and keeps the page id")
            pages: dict[str, str] = {}

            async def fake_post(path, json):
                if path == "/pages":
                    text = "".join(rt["paragraph"]["rich_text"][0]["text"]["content"]
                                   for rt in json.get("children", []))
                    pages["p-1"] = text
                    return {"id": "p-1"}
                return {}

            async def fake_get(path):
                pid = path.split("/")[2]
                return {"results": [{"type": "paragraph", "paragraph": {
                    "rich_text": [{"plain_text": pages.get(pid, "")}]}}]}

            old_post, old_get, old_tok = N._post, N._get, settings.notion_token
            try:
                N._post, N._get, settings.notion_token = fake_post, fake_get, "test-token"
                page = asyncio.run(D.create("Feed brief", BODY, kind="brief",
                                            destination="notion", parent_id="parent-1"))
                check("a Notion page is written and verified", page.verified, page)
                check("...and the page id is kept, not thrown away", page.ref == "p-1", page.ref)

                print("\n[read-back] 06.F2 — a destination that stores nothing is caught")
                pages.clear()                       # accepted the write, kept none of it

                async def amnesiac_post(path, json):
                    return {"id": "p-2"}

                N._post = amnesiac_post
                lost = asyncio.run(D.create("Lost brief", BODY, destination="notion",
                                            parent_id="parent-1"))
                check("it is NOT reported as done", not lost.verified, lost)
                check("...and says the write happened but did not check out",
                      "couldn't read it back" in lost.spoken()
                      or "doesn't match" in lost.why, lost.spoken())
                check("...while still handing over the reference to go look",
                      lost.ref == "p-2", lost.ref)

                print("\n[read-back] a write that never happened is a different sentence")

                async def refusing_post(path, json):
                    raise RuntimeError("parent page is archived")

                N._post = refusing_post
                gone = asyncio.run(D.create("Never written", BODY, destination="notion",
                                            parent_id="parent-1"))
                check("a refused write is not verified", not gone.verified)
                check("...has no reference, because there is nothing to look at", gone.ref == "")
                check("...and says it could not write it",
                      gone.spoken().startswith("I couldn't write"), gone.spoken())
                check("...naming the reason", "archived" in gone.why, gone.why)

                print("\n[read-back] truncation is caught — which is why the fingerprint is long")
                # The failure a short fingerprint would miss: the destination keeps the opening
                # words and silently drops the rest. From the writer's side it looks identical to a
                # clean write, and the owner finds out when he opens the note weeks later.
                async def truncating_post(path, json):
                    text = "".join(rt["paragraph"]["rich_text"][0]["text"]["content"]
                                   for rt in json.get("children", []))
                    pages["p-3"] = text[:30]
                    return {"id": "p-3"}

                N._post = truncating_post
                cut = asyncio.run(D.create("Cut short", BODY, destination="notion",
                                           parent_id="parent-1"))
                check("a destination that keeps only the opening is not verified",
                      not cut.verified, cut)
                check("...and says what came back doesn't match", "doesn't match" in cut.why,
                      cut.why)
            finally:
                N._post, N._get, settings.notion_token = old_post, old_get, old_tok

            print("\n[read-back] the refusals: no title, no body, nowhere to put it")
            for args, why in (
                (dict(title="", body=BODY), "I need a title"),
                (dict(title="Empty", body="   "), "nothing to put in it"),
                (dict(title="Elsewhere", body=BODY, destination="dropbox"), "I can write to"),
            ):
                out = asyncio.run(D.create(destination=args.pop("destination", "local"), **args))
                check(f"refused: {why!r}", why in out.why and not out.verified, out)
            check("a refused document is not recorded as written",
                  all(r["title"] not in ("", "Empty", "Elsewhere") for r in D.created()),
                  [r["title"] for r in D.created()])

            print("\n[three destinations] the two writers it replaces are no longer advertised")
            from afon.brain.tools import group_tool_schemas, groups_for_text, tool_handlers, tool_names

            names = set(tool_names())
            check("create_document is the creation tool", "create_document" in names)
            check("write_vault is no longer offered to the model", "write_vault" not in names)
            check("...but its handler still exists", "write_vault" in tool_handlers())
            check("notion_create_page is no longer offered", "notion_create_page" not in names)
            check("...but its handler still exists", "notion_create_page" in tool_handlers())
            group = {s["function"]["name"] for s in group_tool_schemas("docs")}
            check("it is not paid for on every turn", "create_document" in group, sorted(group))
            for phrase in ("write that up and put it in my vault", "make a note of this",
                           "draft a decision record", "the note you wrote yesterday"):
                check(f"'{phrase}' reaches it", "docs" in groups_for_text(phrase),
                      groups_for_text(phrase))

            print("\n[read-back] creating a document is confirm-gated, like the writers it replaced")
            from afon.brain.proactive import confirm_required

            check("create_document confirms first", confirm_required("create_document"))
        finally:
            settings.state_dir, settings.vault_path = real_state, real_vault
            settings.vault_writable = real_writable

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
