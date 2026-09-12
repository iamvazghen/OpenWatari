"""Push notifications via ntfy — reach the owner when voice isn't available.

When Afon can't speak (PC asleep, he's away from the mic), a reminder or alert is pushed to
his phone via an ntfy topic (https://ntfy.sh or a self-hosted server). Configure
``AFON_NTFY_TOPIC`` (and optionally ``AFON_NTFY_SERVER``). Used both as a tool and by the
proactive scheduler's delivery fallback (see ``brain/scheduler.py``).
"""

from __future__ import annotations

from loguru import logger

from afon.brain.tools.base import http_post, missing_arg, not_configured, tool_error
from afon.config import settings


# ntfy.sh holds scheduled messages server-side and delivers them at the chosen time even if
# this PC is off/asleep — that's the "true 24/7" path for one-shot reminders (Phase 4b). Its
# window is bounded: minimum ~10s out, maximum 3 days ahead (ntfy.sh free tier).
NTFY_MIN_DELAY_S = 10
NTFY_MAX_DELAY_S = 3 * 24 * 3600


def ntfy_can_schedule(epoch_s: float) -> bool:
    """True if `epoch_s` falls inside ntfy.sh's server-side delivery window (and a topic is set)."""
    import time

    if not settings.ntfy_topic:
        return False
    delta = epoch_s - time.time()
    return NTFY_MIN_DELAY_S <= delta <= NTFY_MAX_DELAY_S


async def push(message: str, title: str = "Afon", at: float | None = None) -> bool:
    """Send a push; returns True on success. Safe to call from the scheduler.

    If ``at`` (a Unix epoch in seconds) is given and inside ntfy's window, ntfy holds the
    message and delivers it then — so it still reaches the phone with the PC off (Phase 4b).
    """
    if not settings.ntfy_topic:
        return False
    try:
        url = f"{settings.ntfy_server.rstrip('/')}/{settings.ntfy_topic}"
        headers = {"Title": title}
        if at is not None and ntfy_can_schedule(at):
            headers["At"] = str(int(at))  # ntfy accepts a Unix timestamp for delayed delivery
        await http_post(url, content=message.encode("utf-8"), headers=headers)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"ntfy push failed: {type(e).__name__}: {e}")
        return False


async def send_push(args: dict) -> str:
    message = (args.get("message") or "").strip()
    title = (args.get("title") or "Afon").strip()
    if not message:
        return missing_arg("send_push", args, "message", "text", "body", "title",
                           ask="What should I push, sir?")
    if not settings.ntfy_topic:
        return not_configured("push notifications", "an ntfy topic (AFON_NTFY_TOPIC)")
    try:
        ok = await push(message, title)
        return "Pushed to your phone, sir." if ok else "The push didn't go through, sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("push notification", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "send_push",
            "description": (
                "Send a push notification to the owner's phone (ntfy). Use when something needs to "
                "reach him and speaking isn't enough, or he asks you to ping his phone."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The notification body."},
                    "title": {"type": "string", "description": "Optional title (default 'Afon')."},
                },
                "required": ["message"],
            },
        },
    },
]

HANDLERS = {"send_push": send_push}
