"""Self-health — Afon notices when his own organs are failing and says so (Phase X).

A great assistant knows when he's degraded. This module checks the parts Afon depends on — the L3
Obsidian vault (must always be readable), the L4 cache backend, and the always-on VPS ticker (if
configured) — and exposes two faces:

  * ``check()`` — a structured snapshot for the ``self_health`` tool ("are you all right?").
  * ``health_signals()`` — a proactive signal source: it emits a Signal *only when something is
    actually wrong*, so the tick can surface "I've lost sight of your vault, sir" unprompted (then
    repeat-suppression keeps it from nagging).

Everything is fail-quiet: a check that itself errors is reported as a degraded component, never a
crash.
"""

from __future__ import annotations

import httpx
from loguru import logger

from afon.brain.proactive import Signal
from afon.config import settings


async def _check_vault() -> tuple[bool, str]:
    try:
        from afon.brain.context import validate_vault

        ok, msg = validate_vault()
        return ok, msg
    except Exception as e:  # noqa: BLE001
        return False, f"vault check error: {type(e).__name__}"


async def _check_ticker() -> tuple[bool, str]:
    """The recurring-reminder host. 'not configured' is fine (not a fault); unreachable is a fault."""
    if not settings.ticker_url:
        return True, "ticker not configured (optional)"
    try:
        headers = {}
        if settings.ticker_token:
            headers["Authorization"] = f"Bearer {settings.ticker_token}"
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{settings.ticker_url.rstrip('/')}/health", headers=headers)
            r.raise_for_status()
        return True, "ticker reachable"
    except Exception as e:  # noqa: BLE001
        return False, f"ticker unreachable ({type(e).__name__})"


def _check_pc_link() -> tuple[bool, str]:
    """The laptop executor. Down means device control, the camera and the screen are all unreachable.

    Reported, but deliberately NOT a proactive signal (see ``health_signals``): a closed laptop is a
    normal state of the world, not a fault to be paged about. It belongs in the snapshot because the
    alternative — a spoken "all systems nominal" while half the body is unreachable — is the exact
    overclaim J3.6 was about.
    """
    try:
        from afon.brain.pc_link import PC_LINK

        if PC_LINK.active():
            return True, f"laptop connected ({PC_LINK.host() or 'unknown host'})"
        return False, "laptop executor not connected — device control, camera and screen are dark"
    except Exception as e:  # noqa: BLE001
        return False, f"pc-link check error: {type(e).__name__}"


def _check_cache() -> tuple[bool, str]:
    try:
        from afon.brain.cache import CACHE

        return True, f"cache backend: {CACHE.backend}"
    except Exception as e:  # noqa: BLE001
        return False, f"cache error: {type(e).__name__}"


#: How each component is said out loud. The KEYS are the coverage claim: `summarize()` names exactly
#: what this snapshot looked at, so a component added here cannot silently fall out of the sentence
#: (and one that is never added cannot be implied by it).
_SPOKEN = {"vault": "your vault", "ticker": "reminders", "cache": "the cache",
           "pc_link": "the laptop"}


async def check() -> dict:
    """A health snapshot of the components this process can see: {component: {ok, detail}}.

    Deliberately NOT "everything": the edge's own organs — microphone, speaker, wake word — live in
    another process on another machine and are watched there (``edge/voice_health.py``,
    ``edge/audio_watchdog.py``). A cross-host facade would mean the brain importing edge internals,
    which the layering gate forbids for good reason. What this owes the owner instead is an honest
    account of its own coverage; see ``summarize``.
    """
    vault_ok, vault_msg = await _check_vault()
    ticker_ok, ticker_msg = await _check_ticker()
    cache_ok, cache_msg = _check_cache()
    pc_ok, pc_msg = _check_pc_link()
    return {
        "vault": {"ok": vault_ok, "detail": vault_msg},
        "ticker": {"ok": ticker_ok, "detail": ticker_msg},
        "cache": {"ok": cache_ok, "detail": cache_msg},
        "pc_link": {"ok": pc_ok, "detail": pc_msg},
    }


def summarize(snapshot: dict) -> str:
    """A one-line spoken summary of a health snapshot.

    It used to open with "All systems nominal" over a three-component check — an overclaim the owner
    can act on, and the same defect class as a status page that shows green because it never asked.
    It now names its coverage, so "healthy" is scoped to what was actually looked at.
    """
    bad = [name for name, s in snapshot.items() if not s.get("ok")]
    if not bad:
        seen = ", ".join(_SPOKEN.get(n, n) for n in snapshot)
        return f"Everything I can see is healthy, sir — {seen}."
    parts = [f"{name} ({snapshot[name]['detail']})" for name in bad]
    return "I'm partly degraded, sir: " + "; ".join(parts) + "."


async def health_signals() -> list[Signal]:
    """Proactive source: a Signal per genuinely-failing component (none when all is well)."""
    try:
        snap = await check()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"health snapshot failed: {type(e).__name__}")
        return []
    signals: list[Signal] = []
    if not snap["vault"]["ok"]:
        signals.append(Signal(
            key="health-vault", kind="health", urgency=0.7,
            message=f"Heads up, sir — I've lost read access to your vault: {snap['vault']['detail']}",
        ))
    if not snap["ticker"]["ok"]:
        signals.append(Signal(
            key="health-ticker", kind="health", urgency=0.65,
            message="Sir, your always-on reminder host isn't responding — recurring reminders may not fire.",
        ))
    # No signal for pc_link on purpose: a closed laptop is a normal state of the world. It is
    # reported in the snapshot and spoken when he ASKS how he is, never pushed at him.
    return signals
