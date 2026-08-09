"""Module-level job targets so apscheduler can serialise them."""
import asyncio

from loguru import logger


async def _fire_reliability_probe() -> None:
    """Probe critical organs, self-repair what's fixable, and page the owner on a SUSTAINED
    outage (Phase 0.1). Never raises into the loop."""
    try:
        from afon.brain.reliability import health_probe, attempt_repair_and_escalate
        probes = await health_probe()
        summary = await attempt_repair_and_escalate(probes)
        still_bad = [n for n, s in summary.items() if not s["ok"]]
        repaired = [n for n, s in summary.items() if s.get("repaired")]
        if repaired:
            logger.info(f"reliability probe: self-repaired {repaired}")
        if still_bad:
            logger.warning(f"reliability probe: still degraded after repair: {still_bad}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"reliability probe failed: {type(e).__name__}: {e}")


async def _fire_composio_catalog_refresh() -> None:
    """T12 — refresh the cached Composio catalog so the system prompt picks up new tools
    overnight (before the morning briefing, so the next session sees the full list)."""
    try:
        from afon.brain.composio_catalog import refresh, invalidate
        n = await refresh()
        invalidate()
        total = sum(len(v) for v in (n.get("toolkits") or {}).values())
        logger.info(f"composio catalog nightly refresh: {total} tools cached")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"composio catalog refresh failed: {type(e).__name__}: {e}")