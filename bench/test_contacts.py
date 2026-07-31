"""Contacts & identity resolution — Phase 4.3 (hermetic, temp file, no network).

Verifies the contact book parses the forgiving line formats, resolves a name to its targets, asks a
targeted clarification when a name is unknown or ambiguous, the tool degrades with no file, and the
tool is registered + NOT confirm-gated (it's read-only; the SEND stays gated).

    uv run python bench/test_contacts.py
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


def main() -> None:
    from jarvis.brain.contacts import ContactBook

    tmp = Path(tempfile.mkdtemp(prefix="jarvis-contacts-")) / "contacts.md"
    tmp.write_text(
        "# My contacts\n"
        "- John Smith <john@example.com> tg:@johnsmith tel:+15551234567\n"
        "- Anush | email: anush@work.com | telegram: @anushk\n"
        "- John Baker  john.baker@other.example\n"
        "- The Vet  vet@clinic.example\n",
        encoding="utf-8",
    )
    book = ContactBook(path=tmp)

    print("[1] parsing the forgiving formats")
    people = {c.name: c for c in book.all()}
    check("parsed all four contacts", len(people) == 4, str(list(people)))
    js = people.get("John Smith")
    check("angle-bracket email parsed", js is not None and js.email == "john@example.com")
    check("tg: handle parsed", js is not None and js.telegram == "@johnsmith")
    check("tel: phone parsed", js is not None and js.phone == "+15551234567")
    an = people.get("Anush")
    check("pipe 'email:'/'telegram:' fields parsed",
          an is not None and an.email == "anush@work.com" and an.telegram == "@anushk")

    print("\n[2] resolution: exact / ambiguous / unknown")
    check("unique name resolves to one", len(book.resolve("Anush")) == 1)
    check("a shared first name is ambiguous (two Johns)", len(book.resolve("John")) == 2)
    check("exact full name beats the partial match", [c.name for c in book.resolve("John Smith")] == ["John Smith"])
    check("unknown name resolves to none", book.resolve("Zaphod") == [])

    print("\n[3] the tool: targets / clarify / degrade")
    import jarvis.brain.contacts as contacts_mod
    from jarvis.brain.tools.contacts import resolve_contact

    contacts_mod.BOOK = book  # point the tool at the temp book
    out_one = asyncio.run(resolve_contact({"name": "Anush"}))
    check("resolved contact reports the address", "anush@work.com" in out_one, out_one)
    out_many = asyncio.run(resolve_contact({"name": "John"}))
    check("ambiguous name asks which one", "which one" in out_many.lower(), out_many)
    out_none = asyncio.run(resolve_contact({"name": "Zaphod"}))
    check("unknown name asks for the address", "don't have a contact" in out_none.lower(), out_none)

    contacts_mod.BOOK = ContactBook(path=tmp.parent / "nope.md")
    out_degrade = asyncio.run(resolve_contact({"name": "anyone"}))
    check("no contact file -> graceful note, no crash", "don't have a contact" in out_degrade.lower(),
          out_degrade)

    print("\n[4] saving people so they survive the session")
    # Until 2026-07-30 the book was read-only while resolve_contact told the owner to "say 'save it'
    # and I'll keep them" — so every new person had to be re-dictated next session.
    from jarvis.brain.tools.contacts import save_contact

    save_book = ContactBook(path=tmp.parent / "saved.md")
    contacts_mod.BOOK = save_book
    asyncio.run(save_contact({"name": "Nona", "telegram": "nonak", "email": "nona@example.com"}))
    check("a saved contact resolves afterwards", len(save_book.resolve("Nona")) == 1)
    saved = save_book.resolve("Nona")[0]
    check("a bare handle is stored as @handle", saved.telegram == "@nonak", str(saved.telegram))
    check("the saved email round-trips", saved.email == "nona@example.com", str(saved.email))
    asyncio.run(save_contact({"name": "Nona", "phone": "+15550001111"}))
    check("saving the same name updates instead of duplicating",
          len(save_book.resolve("Nona")) == 1 and save_book.resolve("Nona")[0].phone == "+15550001111")
    out_bare = asyncio.run(save_contact({"name": "Bob"}))
    check("saving with no target at all is refused", "at least" in out_bare.lower(), out_bare)

    print("\n[5] registered + confirm posture (send stays the gated step)")
    from jarvis.brain.proactive import confirm_required
    from jarvis.brain.tools import tool_names

    check("resolve_contact is registered", "resolve_contact" in tool_names())
    check("save_contact is registered", "save_contact" in tool_names())
    check("resolve_contact is NOT confirm-gated (read-only)", not confirm_required("resolve_contact"))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
