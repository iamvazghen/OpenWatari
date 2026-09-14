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

from loguru import logger

from afon.brain.proactive import Signal
from afon.shared.degraded import Announcer
from afon.shared.degraded import Mode as DegradedMode
from afon.shared.degraded import current as degraded_mode
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
        from afon.brain.tools.base import http_get

        await http_get(f"{settings.ticker_url.rstrip('/')}/health", headers=headers)
        return True, "ticker reachable"
    except Exception as e:  # noqa: BLE001
        return False, f"ticker unreachable ({type(e).__name__})"


async def _check_pc_link() -> tuple[bool, str]:
    """The laptop executor. Down means device control, the camera and the screen are all unreachable.

    Reported, but deliberately NOT a proactive signal (see ``health_signals``): a closed laptop is a
    normal state of the world, not a fault to be paged about. It belongs in the snapshot because the
    alternative — a spoken "all systems nominal" while half the body is unreachable — is the exact
    overclaim J3.6 was about.
    """
    try:
        from afon.brain.pc_link import PC_LINK

        if not PC_LINK.active:
            return False, ("laptop executor not connected — device control, camera and screen "
                           "are dark")
        # `active` only says a socket is registered. A laptop that closed its lid leaves that
        # socket open for over a minute, and reporting "connected" on the strength of it is the
        # same overclaim as a status page that shows green because it never asked. So ask.
        if not await PC_LINK.reachable():
            return False, (f"laptop registered but not answering (silent "
                           f"{int(PC_LINK.silent_for)}s) — device control, camera and screen "
                           "are dark")
        return True, f"laptop connected ({PC_LINK.host or 'unknown host'})"
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
           "pc_link": "the laptop", "task_queue": "your task board"}


async def _check_task_queue() -> tuple[bool, str]:
    """The external task-queue pointer (16.F3).

    Its database id went stale for weeks and nothing noticed, because every caller reads a failed
    query as "no tasks": an empty queue and a broken pointer look identical at the call site. The
    visible symptom was a morning brief cheerfully reporting nothing due. Not configured is not a
    fault; a configured pointer that does not resolve is.
    """
    try:
        from afon.brain.tools.notion import queue_pointer_ok

        return await queue_pointer_ok()
    except Exception as e:  # noqa: BLE001
        # A health check that raises takes the whole snapshot with it, which would hide the four
        # components that were fine.
        return False, f"task queue check failed ({type(e).__name__})"


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
    pc_ok, pc_msg = await _check_pc_link()
    queue_ok, queue_msg = await _check_task_queue()
    return {
        "vault": {"ok": vault_ok, "detail": vault_msg},
        "ticker": {"ok": ticker_ok, "detail": ticker_msg},
        "cache": {"ok": cache_ok, "detail": cache_msg},
        "pc_link": {"ok": pc_ok, "detail": pc_msg},
        "task_queue": {"ok": queue_ok, "detail": queue_msg},
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


#: 32.F3 — announce-once state for the degraded mode this host is in. Module-level because a
#: mode is a property of the process, and a fresh announcer per tick would announce every tick.
_DEGRADED = Announcer()


def mode_from(snapshot: dict) -> "DegradedMode":
    """Which declared mode this host is in, from what the health check actually found.

    The brain can see two of the three: the laptop (pc_link) and the network (the vault check is a
    local read, but the ticker and task queue are not — if none of the remote checks can reach
    anything, the network is the honest diagnosis rather than three coincidental failures).
    """
    edge_up = bool(snapshot.get("pc_link", {}).get("ok"))
    remote = [snapshot.get(n, {}).get("ok") for n in ("ticker", "task_queue")]
    network_up = any(r for r in remote) if remote else True
    return degraded_mode(brain_up=True, edge_up=edge_up, network_up=network_up)


async def degraded_signals() -> list[Signal]:
    """32.F3 — one signal when this host ENTERS a degraded mode, and one when it leaves.

    Not one per tick. A degraded mode repeated every tick is noise the owner learns to talk over,
    and it is the recovery he actually needs to hear: without it he keeps working around a
    limitation that has been gone for an hour.
    """
    try:
        snap = await check()
    except Exception:  # noqa: BLE001
        return []
    said = _DEGRADED.update(mode_from(snap))
    if not said:
        return []
    return [Signal(key=f"degraded-{_DEGRADED.mode.name}", kind="health", urgency=0.75,
                   message=said)]


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
    if not snap["task_queue"]["ok"]:
        signals.append(Signal(
            key="health-task-queue", kind="health", urgency=0.7,
            message=("Sir, I can't resolve your task board — "
                     f"{snap['task_queue']['detail']}. Anything I say about what's due is "
                     "incomplete until that's fixed."),
        ))
    # No signal for pc_link on purpose: a closed laptop is a normal state of the world. It is
    # reported in the snapshot and spoken when he ASKS how he is, never pushed at him.
    return signals
