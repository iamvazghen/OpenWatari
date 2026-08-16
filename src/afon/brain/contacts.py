"""Contact book — resolve a NAME to a real send target (Phase 4.3).

"Email John" is useless until "John" becomes an address. This reads a simple local ``contacts.md`` so
Afon can turn a name into an email / Telegram / phone target before a send or draft — and ask a
targeted clarification when a name is unknown or matches more than one person.

`contacts.md` is forgiving plain text, one contact per line. All of these parse:

    - John Smith <john@example.com> tg:@johnsmith tel:+15551234567
    - Anush | email: anush@work.com | telegram: @anushk
    - The Vet  vet@clinic.example

It is local + private (gitignored) and entirely optional: no file -> resolution simply reports the
name is unknown, never crashes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from afon.config import settings
from afon.shared.entities import canonical, is_handle, same_entity

_REPO_ROOT = Path(__file__).resolve().parents[3]

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_TG_RE = re.compile(r"(?:tg:|telegram:\s*)?(@[A-Za-z0-9_]{3,})")
_PHONE_RE = re.compile(r"(?:tel:|phone:\s*)?(\+\d[\d ().-]{6,}\d)")


def _contacts_path() -> Path:
    p = settings.contacts_path
    return Path(p) if p else _REPO_ROOT / "contacts.md"


@dataclass
class Contact:
    name: str
    email: str | None = None
    telegram: str | None = None
    phone: str | None = None

    def targets(self) -> str:
        bits = []
        if self.email:
            bits.append(f"email {self.email}")
        if self.telegram:
            bits.append(f"Telegram {self.telegram}")
        if self.phone:
            bits.append(f"phone {self.phone}")
        return ", ".join(bits) or "no contact details on file"


def _parse_line(line: str) -> Contact | None:
    raw = line.strip().lstrip("-*").strip()
    if not raw or raw.startswith("#"):
        return None
    email = _EMAIL_RE.search(raw)
    email_val = email.group(0) if email else None
    # Pull the name = leading text before the first delimiter / address / handle.
    name = re.split(r"[<|]|tg:|telegram:|tel:|phone:|\S+@", raw, maxsplit=1)[0].strip(" -|:")
    if not name:
        name = raw.split()[0]
    # Remove the email first so its "@domain" can't be mistaken for a Telegram handle.
    rest = raw.replace(email_val, " ") if email_val else raw
    tg = _TG_RE.search(rest)
    phone = _PHONE_RE.search(rest)
    return Contact(
        name=name,
        email=email_val,
        telegram=tg.group(1) if tg else None,
        phone=phone.group(1) if phone else None,
    )


def _targets_of(contact: "Contact") -> list[str]:
    """Every channel that identifies this person — the addresses a query might arrive as."""
    return [v for v in (getattr(contact, f, "") or "" for f in ("email", "telegram", "phone")) if v]


class ContactBook:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _contacts_path()

    def all(self) -> list[Contact]:
        try:
            text = self._path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return []
        out = []
        for line in text.splitlines():
            c = _parse_line(line)
            if c:
                out.append(c)
        return out

    def save(self, contact: Contact) -> str:
        """Append (or update) one contact and return what was stored.

        The book was read-only until 2026-07-30, even though resolve_contact told the owner to "say
        'save it' and I'll keep them for next time" — so every new person had to be re-dictated in the
        next session. Writing the same forgiving one-line format the parser reads keeps the file
        hand-editable."""
        bits = [contact.name]
        if contact.email:
            bits.append(f"<{contact.email}>")
        if contact.telegram:
            bits.append(f"tg:{contact.telegram}")
        if contact.phone:
            bits.append(f"tel:{contact.phone}")
        line = "- " + " ".join(bits)
        existing = [c for c in self.all() if c.name.lower() == contact.name.lower()]
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if existing:  # replace that person's line rather than growing a second entry
                text = self._path.read_text(encoding="utf-8", errors="ignore")
                kept = [ln for ln in text.splitlines()
                        if not ((p := _parse_line(ln)) and p.name.lower() == contact.name.lower())]
                self._path.write_text("\n".join(kept + [line]) + "\n", encoding="utf-8")
            else:
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(("" if not self._path.stat().st_size else "") + line + "\n")
        except OSError as e:
            raise RuntimeError(f"could not write the contact book: {e}") from e
        return line

    def resolve(self, query: str) -> list[Contact]:
        """Contacts matching ``query`` by name, spelling variant, or channel (24.F1).

        Four passes, narrowest first, so a precise query never widens into an ambiguous one:
        an exact canonical name; a CHANNEL (an email/handle/phone identifies exactly one person);
        a close spelling; then the old any-word fallback for partial names ("call Anna").
        """
        q = (query or "").strip()
        if not q:
            return []
        people = self.all()
        key = canonical(q)

        exact = [c for c in people if canonical(c.name) == key]
        if exact:
            return exact
        if is_handle(q):
            # A channel names one person. Matching it by name-similarity afterwards would be
            # nonsense, so this returns whatever it finds — including nothing.
            return [c for c in people if any(canonical(t) == key for t in _targets_of(c))]
        spelled = [c for c in people if same_entity(c.name, q)]
        if spelled:
            return spelled
        qwords = set(key.split())
        return [c for c in people
                if qwords & set(canonical(c.name).split()) or key in canonical(c.name)]


BOOK = ContactBook()
