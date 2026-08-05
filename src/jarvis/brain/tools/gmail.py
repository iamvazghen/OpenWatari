"""Gmail tools — read, draft, and send mail through the owner's Google app (Phase 11).

Backed by ``brain/google.py`` (one OAuth app, shared with Calendar). ``read_email`` summarises
unread or searched mail; ``draft_email`` saves a draft; ``send_email`` actually sends and is
**confirm-gated** (outward-facing — the persona/confirm policy makes Jarvis read it back and get a
yes first). Everything degrades to a spoken "not configured" note until the one-time login is done.
"""

from __future__ import annotations

import base64
from email.message import EmailMessage

from jarvis.brain.google import api_get, api_post, configured
from jarvis.brain.tools.base import clip, not_configured, tool_error

_GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
_NEEDS = "your Google login — run bench/google_login.py once (JARVIS_GOOGLE_* keys)"


def _b64url(msg: EmailMessage) -> str:
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


def _build_mime(to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


async def read_email(args: dict) -> str:
    if not configured():
        return not_configured("Gmail", _NEEDS)
    query = (args.get("query") or "is:unread").strip()
    try:
        max_n = int(args.get("max") or 5)
    except (TypeError, ValueError):
        max_n = 5
    try:
        listing = await api_get(f"{_GMAIL}/messages", params={"q": query, "maxResults": max_n})
        ids = [m["id"] for m in (listing.get("messages") or [])]
        if not ids:
            return f"No mail matching '{query}', sir."
        out: list[str] = []
        for mid in ids[:max_n]:
            msg = await api_get(
                f"{_GMAIL}/messages/{mid}",
                params={"format": "metadata", "metadataHeaders": ["From", "Subject"]},
            )
            headers = {h["name"].lower(): h["value"] for h in (msg.get("payload", {}).get("headers") or [])}
            frm = headers.get("from", "unknown")
            subj = headers.get("subject", "(no subject)")
            snippet = clip(msg.get("snippet", ""), 140)
            out.append(f"From {frm}: {subj} — {snippet}")
        return f"You have {len(out)} message(s), sir. " + " | ".join(out)
    except Exception as e:  # noqa: BLE001
        return tool_error("email read", e)


async def email_signals():
    """Proactive source: one daily nudge if important mail is unread (Gmail's own IMPORTANT marker,
    not a guess). Repeat-suppressed by date. Fail-quiet — no login/empty/error -> no signals."""
    from datetime import date

    from jarvis.brain.proactive import Signal

    if not configured():
        return []
    try:
        listing = await api_get(f"{_GMAIL}/messages",
                                params={"q": "is:important is:unread newer_than:1d", "maxResults": 5})
        ids = [m["id"] for m in (listing.get("messages") or [])]
        if not ids:
            return []
        msg = await api_get(f"{_GMAIL}/messages/{ids[0]}",
                            params={"format": "metadata", "metadataHeaders": ["From"]})
        headers = {h["name"].lower(): h["value"] for h in (msg.get("payload", {}).get("headers") or [])}
        frm = headers.get("from", "someone")
    except Exception:  # noqa: BLE001 — never throw into the tick loop
        return []
    msg = (f"Sir, you have {len(ids)} important email{'s' if len(ids) > 1 else ''} unread, "
           f"the latest from {frm.split('<')[0].strip()}.")
    return [Signal(key=f"email-{date.today()}", kind="thread", urgency=0.6, message=msg)]


async def important_email_phrase() -> str:
    """A one-line phrase about important unread mail ('3 important emails unread, the latest from
    Jane') for the daily digest, or '' if none / not configured. Fail-quiet."""
    if not configured():
        return ""
    try:
        listing = await api_get(f"{_GMAIL}/messages",
                                params={"q": "is:important is:unread newer_than:1d", "maxResults": 5})
        ids = [m["id"] for m in (listing.get("messages") or [])]
        if not ids:
            return ""
        msg = await api_get(f"{_GMAIL}/messages/{ids[0]}",
                            params={"format": "metadata", "metadataHeaders": ["From"]})
        headers = {h["name"].lower(): h["value"] for h in (msg.get("payload", {}).get("headers") or [])}
        frm = headers.get("from", "someone").split("<")[0].strip()
    except Exception:  # noqa: BLE001 — never throw into the digest
        return ""
    n = len(ids)
    return f"{n} important email{'s' if n != 1 else ''} unread, the latest from {frm}"


async def draft_email(args: dict) -> str:
    if not configured():
        return not_configured("Gmail", _NEEDS)
    to = (args.get("to") or "").strip()
    subject = (args.get("subject") or "").strip()
    body = (args.get("body") or "").strip()
    if not (to and body):
        return "I need at least a recipient and a body to draft that, sir."
    try:
        raw = _b64url(_build_mime(to, subject, body))
        await api_post(f"{_GMAIL}/drafts", {"message": {"raw": raw}})
        return f"Draft to {to} saved, sir — say the word and I'll send it."
    except Exception as e:  # noqa: BLE001
        return tool_error("email draft", e)


async def send_email(args: dict) -> str:
    if not configured():
        return not_configured("Gmail", _NEEDS)
    to = (args.get("to") or "").strip()
    subject = (args.get("subject") or "").strip()
    body = (args.get("body") or "").strip()
    if not (to and body):
        return "I need a recipient and a body before I can send, sir."
    try:
        raw = _b64url(_build_mime(to, subject, body))
        await api_post(f"{_GMAIL}/messages/send", {"raw": raw})
        return f"Sent, sir — your message to {to} is on its way."
    except Exception as e:  # noqa: BLE001
        return tool_error("email send", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "read_email",
            "description": (
                "Read the owner's Gmail — unread by default, or a Gmail search query "
                "(e.g. 'from:bank', 'is:unread newer_than:2d'). Summarises sender, subject, snippet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Gmail search query; default is:unread."},
                    "max": {"type": "integer", "description": "How many to summarise (default 5)."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "draft_email",
            "description": "Save a Gmail draft WITHOUT sending it, so the owner can review first. "
                           "Use for 'draft an email to X', 'write it but don't send'. send_email "
                           "actually sends — never substitute one for the other.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address."},
                    "subject": {"type": "string", "description": "Subject line."},
                    "body": {"type": "string", "description": "Message body."},
                },
                "required": ["to", "body"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": (
                "Send an email via Gmail. OUTWARD-FACING: first read the recipient, subject, and "
                "gist back to the owner and get a clear yes — only then call this."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email address."},
                    "subject": {"type": "string", "description": "Subject line."},
                    "body": {"type": "string", "description": "Message body."},
                },
                "required": ["to", "body"],
            },
        },
    },
]

HANDLERS = {"read_email": read_email, "draft_email": draft_email, "send_email": send_email}
