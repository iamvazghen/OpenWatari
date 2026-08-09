# Home Assistant control (Phase 11 / TODO 4.10)

Afon controls a **local** Home Assistant instance over its REST API — the privacy-respecting smart-
home choice (no cloud vendor in the loop). Two tools:

| Tool | Purpose | Confirm? |
|---|---|---|
| `ha_state` | Read one entity's state ("is the front door locked?") | no (read) |
| `ha_call`  | Call a service to actuate (`domain`/`service`/`entity`) | **domain-aware** |

## Confirmation policy (enforced, not just prompted)

`proactive.confirm_required` gates `ha_call` by domain, mirroring `smarthome.SENSITIVE_DOMAINS`:

- **Confirms first:** `lock`, `alarm_control_panel`, `cover`, `garage_door` — the security edges.
- **Flows immediately:** `light`, `scene`, `climate`, `switch`, … — a voice home shouldn't nag you
  to turn on a lamp.

This is real enforcement in code (the agent holds the sensitive call until the owner affirms), not a
prompt suggestion a weak model could skip. Verified in `bench/test_phase11_integrations.py` [7].

## Setup

1. In Home Assistant: **Profile → Long-Lived Access Tokens → Create Token**.
2. Put these in the brain's `.env` (never commit them):
   ```
   AFON_HA_URL=http://homeassistant.local:8123
   AFON_HA_TOKEN=<long-lived token>
   ```
   Use the LAN URL — the brain and HA should share a trusted network (or the tailnet).

## Graceful degradation

With no URL/token set, both tools return a spoken "not configured" note instead of erroring — so the
assistant stays healthy on a machine with no smart home. Verified in
`bench/test_phase11_integrations.py` [4]/[5].

## Runtime acceptance (needs a live HA instance — not in the offline suite)

- [ ] `ha_state` on a known entity (e.g. `light.living_room`) reports its real state.
- [ ] "Turn on the living room light" actuates immediately (no confirmation).
- [ ] "Unlock the front door" asks for confirmation first, then acts only on "yes".
- [ ] A bad token yields a spoken error, not a crash.

## Common entity/service reference

```
light/turn_on         entity: light.living_room
light/turn_off        entity: light.living_room
scene/turn_on         entity: scene.movie_night
climate/set_temperature   (data: temperature)
lock/lock             entity: lock.front_door        # confirms first
lock/unlock           entity: lock.front_door        # confirms first
cover/open_cover      entity: cover.garage           # confirms first
alarm_control_panel/arm_away                          # confirms first
```
