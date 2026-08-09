"""Contacts tool — resolve a name to a send target before an outward action (Phase 4.3).

Afon calls this BEFORE send_email / send_telegram when the user names a person ("email John"): it
turns the name into an address/handle, asks a targeted clarification when the name is unknown or
ambiguous, and so an outward send always goes to a confirmed recipient (the send itself stays
confirm-gated). Read-only and degrades to a clear note when no contact book is configured.
"""

from __future__ import annotations

from afon.brain import contacts as _contacts
from afon.brain.tools.base import tool_error


async def resolve_contact(args: dict) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        return "Who should I look up, sir?"
    matches = _contacts.BOOK.resolve(name)
    if not matches:
        return (f"I don't have a contact for '{name}', sir — what's their email or Telegram? "
                "Give me it and I'll save them with save_contact so they're there next time.")
    if len(matches) > 1:
        names = "; ".join(c.name for c in matches[:6])
        return f"I have a few matches for '{name}', sir: {names}. Which one?"
    c = matches[0]
    return f"{c.name}: {c.targets()}, sir."


async def save_contact(args: dict) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        return "Whose details should I save, sir?"
    c = _contacts.Contact(
        name=name,
        email=(args.get("email") or "").strip() or None,
        telegram=(args.get("telegram") or "").strip() or None,
        phone=(args.get("phone") or "").strip() or None,
    )
    if not (c.email or c.telegram or c.phone):
        return f"I need at least an email, Telegram handle or phone number for {name}, sir."
    if c.telegram and not c.telegram.startswith("@"):
        c.telegram = "@" + c.telegram
    try:
        _contacts.BOOK.save(c)
    except RuntimeError as e:
        # Phrased to match tool_failed()'s "i couldn't complete the" marker while keeping the real
        # cause. The old wording read well but was invisible to that check, so a digest or proactive
        # caller would have happily read the failure aloud as though it were data.
        return f"I couldn't complete the contact save just now, sir — {e}."
    except Exception as e:  # noqa: BLE001 — save() writes JSON to disk: OSError, encoding, disk-full
        return tool_error("contact save", e)
    return f"Saved, sir: {c.name} — {c.targets()}. I'll remember them next time."


SCHEMAS = [
    {"type": "function", "function": {
        "name": "save_contact",
        "description": "Remember a person's email / Telegram / phone so they can be messaged by NAME "
                       "in future sessions. Call this when the owner gives you someone's details, or "
                       "says 'save it' / 'remember him' after you asked who they are. Saving again "
                       "under the same name updates that person rather than duplicating them.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "The person's name, as the owner says it."},
            "email": {"type": "string", "description": "Their email address, if known."},
            "telegram": {"type": "string", "description": "Their Telegram handle, e.g. @someone."},
            "phone": {"type": "string", "description": "Their phone number in +country format."}},
            "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "resolve_contact",
        "description": "Resolve a person's NAME to their email / Telegram / phone before sending or "
                       "drafting to them. Call this first whenever the user names a recipient ('email "
                       "John', 'message Anush') so the send goes to the right address. Returns the "
                       "contact's details, or asks which person if the name is unknown or ambiguous.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "The person's name to resolve."}},
            "required": ["name"]}}},
]

HANDLERS = {"resolve_contact": resolve_contact, "save_contact": save_contact}
