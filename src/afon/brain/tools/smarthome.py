"""Home Assistant tools — local-first smart-home control (Phase 11).

The owner asked for smart-home; Home Assistant is the privacy-respecting, local choice. Talks to his
HA instance's REST API with a long-lived token (``AFON_HA_URL`` / ``AFON_HA_TOKEN``).
``ha_state`` reads ("is the front door locked?"); ``ha_call`` actuates (lights, scenes, climate,
locks). Security-sensitive domains (see ``SENSITIVE_DOMAINS``) are ENFORCED as confirm-gated in
``proactive.confirm_required`` — a lock/alarm/cover/garage call is held until the owner affirms —
while a light or scene flows without friction (a voice home shouldn't nag to turn on a lamp).
Degrades to a spoken note when HA isn't configured. See docs/home-assistant.md for setup + checks.
"""

from __future__ import annotations

import httpx

from afon.brain.tools.base import clip, not_configured, tool_error
from afon.config import settings

_NEEDS = "a Home Assistant URL + long-lived token (AFON_HA_URL / AFON_HA_TOKEN)"
# Domains where actuation is security-sensitive — Afon confirms before calling these.
SENSITIVE_DOMAINS = {"lock", "alarm_control_panel", "cover", "garage_door"}


def _configured() -> bool:
    return bool(settings.ha_url and settings.ha_token)


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.ha_token}", "Content-Type": "application/json"}


async def ha_state(args: dict) -> str:
    if not _configured():
        return not_configured("Home Assistant", _NEEDS)
    entity = (args.get("entity") or "").strip()
    if not entity:
        return "Which device should I check, sir? Give me its entity id (e.g. lock.front_door)."
    try:
        url = f"{settings.ha_url.rstrip('/')}/api/states/{entity}"
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as c:
            r = await c.get(url, headers=_headers())
            r.raise_for_status()
            data = r.json()
        name = (data.get("attributes") or {}).get("friendly_name", entity)
        return f"{name} is {data.get('state', 'unknown')}, sir."
    except Exception as e:  # noqa: BLE001
        return tool_error("smart-home state", e)


async def ha_call(args: dict) -> str:
    if not _configured():
        return not_configured("Home Assistant", _NEEDS)
    domain = (args.get("domain") or "").strip()
    service = (args.get("service") or "").strip()
    entity = (args.get("entity") or "").strip()
    if not (domain and service):
        return "I need a domain and service to call, sir (e.g. light / turn_on)."
    try:
        url = f"{settings.ha_url.rstrip('/')}/api/services/{domain}/{service}"
        payload = {"entity_id": entity} if entity else {}
        async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as c:
            r = await c.post(url, headers=_headers(), json=payload)
            r.raise_for_status()
            changed = r.json()
        target = entity or "the requested devices"
        note = " (security device — I confirmed first)" if domain in SENSITIVE_DOMAINS else ""
        return f"Done, sir — {domain}.{service} on {target}{note}. {clip(str(len(changed))+' entity change(s).', 60)}"
    except Exception as e:  # noqa: BLE001
        return tool_error("smart-home call", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "ha_state",
            "description": (
                "Read the current state of a Home Assistant device by entity id "
                "('is the front door locked?', 'is the living room light on?')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string", "description": "Entity id, e.g. lock.front_door."}
                },
                "required": ["entity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ha_call",
            "description": (
                "Call a Home Assistant service to control a device: domain + service (+ optional "
                "entity). Examples: light/turn_on, climate/set_temperature, lock/lock, scene/turn_on. "
                "For locks, alarms, covers and garage doors, confirm with the owner before calling."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {"type": "string", "description": "Service domain, e.g. light, lock, scene."},
                    "service": {"type": "string", "description": "Service name, e.g. turn_on, lock."},
                    "entity": {"type": "string", "description": "Target entity id (optional)."},
                },
                "required": ["domain", "service"],
            },
        },
    },
]

HANDLERS = {"ha_state": ha_state, "ha_call": ha_call}
