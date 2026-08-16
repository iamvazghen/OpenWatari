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
    from afon.brain.contacts import ContactBook

    tmp = Path(tempfile.mkdtemp(prefix="afon-contacts-")) / "contacts.md"
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
    import afon.brain.contacts as contacts_mod
    from afon.brain.tools.contacts import resolve_contact

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
    from afon.brain.tools.contacts import save_contact

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
    from afon.brain.proactive import confirm_required
    from afon.brain.tools import tool_names

    check("resolve_contact is registered", "resolve_contact" in tool_names())
    check("save_contact is registered", "save_contact" in tool_names())
    check("resolve_contact is NOT confirm-gated (read-only)", not confirm_required("resolve_contact"))

    entity_resolution()

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


# ── 24.F1: one person, one node, however their name arrived ──────────────────────────────────
# Three stores keyed on a person's name and each normalised it differently, so the same human
# arrived as several entities holding a third of the facts each. The two ways that happens to a
# VOICE assistant in particular: STT transliterates a name ("Вазген" -> "Vazghen"), and a person
# is referred to by channel — an address or a handle — as often as by name.
def entity_resolution() -> None:
    import pathlib
    import tempfile

    from afon.brain.contacts import Contact, ContactBook
    from afon.shared.entities import canonical, is_handle, same_entity

    print("\n[24.F1] a name is canonicalised, however it was spelled")
    check("transliteration folds", canonical("Вазген") == canonical("Vazgen"), canonical("Вазген"))
    check("a spelling variant folds", same_entity("Vazghen", "Вазген"))
    check("accents fold", canonical("José García") == "jose garcia", canonical("José García"))
    check("a title is not identity", canonical("Mr. John Smith") == canonical("john smith"))
    check("case and spacing fold", canonical("  ANNA   K.  ") == canonical("anna k"))
    # The threshold, held to the measurements it was chosen from. Transliteration variants of one
    # name score 0.83-0.94; different people score 0.64-0.80. That gap is narrow enough that it is
    # worth pinning both sides of it here, because moving the constant is a one-character edit and
    # the failure it causes — a message to the wrong human — is silent.
    print("\n[24.F1] the similarity band, both sides of it")
    for a, b in [("Yaroslaw", "Ярослав"), ("Sergey", "Sergei"), ("Dmitriy", "Dmitri"),
                 ("Katharina", "Katarina")]:
        check(f"{a} / {b} are the same person", same_entity(a, b))
    for a, b in [("Anna", "Anne"), ("Marc", "Mark"), ("Jan", "Jon")]:
        check(f"{a} / {b} are NOT", not same_entity(a, b),
              "merging two people sends an outward message to the wrong human")
    # Full names get a word-wise rule: a shared surname alone drags two different people to 0.800
    # on whole-string similarity, which is inside touching distance of the merge band.
    check("a shared surname is not identity", not same_entity("John Smith", "Jane Smith"),
          "'john smith' vs 'jane smith' scores 0.800 as a whole string; john/jane is 0.500")
    # These two are where the word-wise rule earns its place, and the reason it exists at all.
    # A LONG shared surname drags the whole-string score to 0.917 — comfortably inside the merge
    # band — while the given names that actually distinguish the two people score 0.750. Planting
    # the rule's removal showed nothing until these cases were added: John/Jane happened to fall
    # below the threshold on its own, so the rule looked load-bearing when it was not being tested.
    check("a long shared surname does not merge two people (Anna/Anne Petrova)",
          not same_entity("Anna Petrova", "Anne Petrova"),
          "whole-string 0.917, given names 0.750 — string similarity alone merges them")
    check("...same shape with Marc/Mark Petrova", not same_entity("Marc Petrova", "Mark Petrova"))
    check("...nor a shared given name", not same_entity("Anna Petrova", "Anna Ivanova"))
    check("...in either position", not same_entity("Anna Petrova", "Boris Petrova"))
    check("but a full name whose every word is a variant still merges",
          same_entity("Sergey Dmitriy", "Sergei Dmitri"))
    check("two different addresses never merge", not same_entity("a@b.com", "c@d.com"))

    print("\n[24.F1] a channel identifies a person too")
    for token, kind in (("a@b.com", "email"), ("@vazgen", "telegram"), ("+37411223344", "phone")):
        check(f"{token} is recognised as a {kind}", is_handle(token))
    check("a plain name is not a channel", not is_handle("Vazgen Sargsyan"))

    with tempfile.TemporaryDirectory() as td:
        book = ContactBook(pathlib.Path(td) / "contacts.md")
        book.save(Contact(name="Вазген Саргсян", email="vaz@example.com", telegram="@vazgen"))
        book.save(Contact(name="Anna Petrova", email="anna@example.com"))

        print("\n[24.F1] the contact book resolves all three ways to the SAME person")
        for query in ("Вазген Саргсян", "Vazgen Sargsyan", "vazgen sargsyan"):
            hits = book.resolve(query)
            check(f"{query!r} resolves to exactly one contact",
                  len(hits) == 1 and canonical(hits[0].name).startswith("vazgen"),
                  f"{len(hits)} hits: {[c.name for c in hits]}")
        for channel in ("vaz@example.com", "@vazgen"):
            hits = book.resolve(channel)
            check(f"the channel {channel!r} resolves to the person who owns it",
                  len(hits) == 1 and canonical(hits[0].name).startswith("vazgen"),
                  f"{[c.name for c in hits]}")
        check("an unknown channel resolves to nobody rather than the nearest name",
              book.resolve("stranger@example.com") == [],
              "guessing which contact an unknown address belongs to is how mail goes astray")
        check("a partial first name still works", len(book.resolve("Anna")) == 1)
        check("an unrelated name finds nothing", book.resolve("Zebedee") == [])


if __name__ == "__main__":
    main()
