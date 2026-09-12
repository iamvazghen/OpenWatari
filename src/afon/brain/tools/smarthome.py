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


from afon.brain.tools.base import http_get, http_post, not_configured, tool_error
from afon.config import settings

_NEEDS = "a Home Assistant URL + long-lived token (AFON_HA_URL / AFON_HA_TOKEN)"

#: Domains where actuation reaches past the owner (28.F3). The first four are security: a lock, an
#: alarm, a blind or a garage door is a decision about who can get in. `climate` and `water_heater`
#: were added because heating is not the owner's alone either — everyone in the building lives in
#: the temperature he sets, and unlike a lamp nobody else can simply undo it from the wall.
SENSITIVE_DOMAINS = {"lock", "alarm_control_panel", "cover", "garage_door",
                     "climate", "water_heater"}


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
        data = (await http_get(url, headers=_headers())).json()
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
        changed = (await http_post(url, headers=_headers(), json=payload)).json()
        return await _read_back(domain, service, entity, changed)
    except Exception as e:  # noqa: BLE001
        return tool_error("smart-home call", e)


#: What each service is trying to make true, so a read-back can say whether it happened. Only the
#: services with an unambiguous target state are listed: `toggle` has none by definition, and
#: guessing one would let Afon claim a result he cannot check.
_EXPECTED = {"turn_on": "on", "turn_off": "off", "lock": "locked", "unlock": "unlocked",
             "open_cover": "open", "close_cover": "closed"}


def _state_of(changed: list, entity: str) -> tuple[str, str] | None:
    """(friendly name, state) for `entity` in a service-call response, or None if it isn't there."""
    for row in changed if isinstance(changed, list) else []:
        if isinstance(row, dict) and row.get("entity_id") == entity:
            attrs = row.get("attributes") or {}
            return str(attrs.get("friendly_name") or entity), str(row.get("state") or "unknown")
    return None


async def _read_back(domain: str, service: str, entity: str, changed: list) -> str:
    """Report the state Afon OBSERVED, not the command he sent (28.F2).

    Home Assistant answers 200 to a service call for a device that is unplugged, out of battery, or
    simply not listening, and returns an empty change list. "Done, sir" over that is a claim about
    the world made from the absence of an error — the same shape as a deploy script that reports
    success because the upload finished. So: look at what actually changed, and when nothing did,
    go and read the entity.
    """
    note = " I confirmed first, since that one isn't only yours." if domain in SENSITIVE_DOMAINS else ""
    if not entity:
        # A service call with no entity id is a scene or a whole-domain sweep; there is no single
        # thing to read back, so say what was touched and claim nothing more.
        n = len(changed) if isinstance(changed, list) else 0
        return f"Ran {domain}.{service}, sir — {n} entity change(s).{note}"

    seen = _state_of(changed, entity)
    if seen is None:
        try:
            url = f"{settings.ha_url.rstrip('/')}/api/states/{entity}"
            data = (await http_get(url, headers=_headers())).json()
            seen = (str((data.get("attributes") or {}).get("friendly_name") or entity),
                    str(data.get("state") or "unknown"))
        except Exception as e:  # noqa: BLE001
            return (f"I sent {domain}.{service} to {entity}, sir, but couldn't read it back to "
                    f"confirm ({type(e).__name__}) — so I can't tell you it worked.{note}")

    name, state = seen
    want = _EXPECTED.get(service)
    if want and state != want:
        # The honest half of read-back: saying so when the device did not do it.
        return (f"I sent {domain}.{service} to {name}, sir, but it still reads {state}. "
                f"Something's wrong with it.{note}")
    return f"{name} is {state}, sir.{note}"


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
