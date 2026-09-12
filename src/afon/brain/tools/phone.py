"""Real-world reach — Twilio phone calls (the single biggest AFON gap, 2026-07-28).

``place_call`` rings a number and speaks a message in a natural voice — "call the restaurant and
book a table", relay a code. Outward-facing and confirm-gated (see ``proactive.CONFIRM_TIER``).
Degrades to a spoken not-configured note until the Twilio account lands (PARKED 2026-07-29: the
owner will create it when calling is needed). SMS/WhatsApp were removed the same day — owner's
call: calls are the capability that matters; Telegram covers texting.

Plain REST via httpx — no twilio SDK dependency. 'to' accepts an E.164 number ("+4915...") or
"me"/"owner" for the owner's own phone.
"""

from __future__ import annotations

from afon.config import settings
from afon.brain.tools.base import clip, http_post, not_configured, tool_error

_NEEDS = ("a Twilio account SID + auth token + a Twilio phone number (AFON_TWILIO_ACCOUNT_SID, "
          "AFON_TWILIO_AUTH_TOKEN, AFON_TWILIO_FROM_NUMBER)")
_API = "https://api.twilio.com/2010-04-01/Accounts/{sid}/{res}.json"


def _configured() -> bool:
    return bool(settings.twilio_account_sid and settings.twilio_auth_token
                and settings.twilio_from_number)


def _resolve_to(raw: str) -> str | None:
    to = (raw or "").strip()
    if to.lower() in {"me", "owner", "my phone"}:
        return settings.owner_phone_number
    return to if to.startswith("+") else None


async def _post(resource: str, data: dict) -> dict:

    auth = (settings.twilio_account_sid, settings.twilio_auth_token)
    url = _API.format(sid=settings.twilio_account_sid, res=resource)
    r = await http_post(url, data=data, auth=auth)
    return r.json()


async def place_call(args: dict) -> str:
    """Ring a number and SPEAK a message (one-way voice note by phone). Relaying a live two-way
    conversation is a later phase — this covers 'call the restaurant and tell them X' / OTP relay."""
    if not _configured():
        return not_configured("Phone calls (Twilio)", _NEEDS)
    to = _resolve_to(args.get("to") or "")
    message = (args.get("message") or "").strip()
    if not to:
        return "I need the number to call in international format (+49...), sir."
    if not message:
        return "What should I say on the call, sir?"
    from xml.sax.saxutils import escape

    twiml = (f'<Response><Pause length="1"/><Say voice="Polly.Brian">{escape(clip(message, 800))}'
             f"</Say></Response>")
    try:
        r = await _post("Calls", {"To": to, "From": settings.twilio_from_number, "Twiml": twiml})
        return f"Calling {to} now, sir — I'll deliver the message. (call {r.get('sid', '')[:10]})"
    except Exception as e:  # noqa: BLE001
        return tool_error("phone call", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "place_call",
            "description": (
                "PLACE A REAL PHONE CALL to a number and speak a message on the owner's behalf — "
                "'call the restaurant and book a table', 'ring my brother and tell him X', relay a "
                "code. Outward-facing: confirm the number and message with the owner first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "E.164 number (+4915...) or 'me'."},
                    "message": {"type": "string", "description": "What to say on the call."},
                },
                "required": ["to", "message"],
            },
        },
    },
]

HANDLERS = {"place_call": place_call}
