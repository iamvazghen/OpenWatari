# External specialist fleet (OPTIONAL) — TEMPLATE

> Only relevant if you run your OWN external agent fleet (e.g. an OpenClaw gateway) for deep,
> multi-step domain work. **It ships fully disabled with no host baked in.** Most users delete this
> file and never think about it. To use it: copy to `openclaw-fleet.md`, set the gateway env vars
> (`AFON_OPENCLAW_GATEWAY_URL`, token, router agent), and flip `AFON_FLEET_AUTHORIZED=true`.

## The pattern
Your assistant stays itself; for work it can't do alone it **consults** the fleet by messaging a
single **router/team-lead** agent (default `ispir`) through the gateway, waits for the reply, then
re-voices the result in its own words. It never addresses a specialist directly — the router owns
task framing and dispatch. The `delegate_to_fleet` tool has no per-call agent parameter by design.

## Describe your fleet here
- Router/team-lead agent name and what it dispatches to.
- Your specialist domains (finance, research, dev, etc.) — one line each.
- Transport notes (gateway RPC + wait, streaming events). Auth token lives in `.env`, not here.

## When NOT to delegate
Quick facts, chit-chat, recall, a single web lookup, your own notes — use the built-in tools.
Delegate only when the work is genuinely multi-step / specialist / needs more than one tool pass.
