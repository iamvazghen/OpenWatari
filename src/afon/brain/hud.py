"""Phase 5.3 — the ambient HUD: one live snapshot of what Afon is holding for you.

A read-only status surface — objectives he's driving, what he's working on right now, anything waiting
on your approval, and where your attention is — served as JSON at ``/hud.json`` and rendered by
``clients/hud/index.html``. It only READS the same singletons the brain already runs on, so it can never
perturb a live turn. Every section is fail-quiet: a broken source yields an empty section, never a 500.

ponytail: no new datastore — this is a projection of OBJECTIVES / TASKS / APPROVALS / PRESENCE. The page
polls this endpoint on an interval; there's no push channel here (add SSE only if a few-second lag ever bites).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from afon.config import settings


def _objectives(book) -> list[dict]:
    out: list[dict] = []
    for o in book.active()[:8]:
        latest = o.progress[-1]["note"] if o.progress else "not started yet"
        out.append({
            "text": o.text,
            "project": o.project,
            "latest": latest,
            "awaiting": list(o.deferred[:3]),
            "updated": o.updated,
        })
    return out


def _working_on(tasks) -> list[dict]:
    out: list[dict] = []
    for t in tasks.active()[:8]:
        out.append({
            "title": t.title,
            "elapsed": t.human_elapsed(),
            "note": (t.last_progress or "").strip()[:140],
            "kind": t.kind,
        })
    return out


def _awaiting_approval(approvals) -> list[dict]:
    out: list[dict] = []
    for a in approvals.pending()[:8]:
        out.append({"summary": a.summary, "origin": a.origin})
    return out


def hud_snapshot(
    *,
    objectives: Any | None = None,
    tasks: Any | None = None,
    approvals: Any | None = None,
    presence: Any | None = None,
    now: datetime | None = None,
) -> dict:
    """Gather the live HUD state. Sources default to the process singletons; injectable for tests.
    Each section is independently fail-quiet — one bad source never sinks the whole snapshot."""
    if objectives is None:
        from afon.brain.objectives import OBJECTIVES as objectives
    if tasks is None:
        from afon.brain.tasks import TASKS as tasks
    if approvals is None:
        from afon.brain.approvals import APPROVALS as approvals
    if presence is None:
        from afon.brain.presence import PRESENCE as presence

    now = now or datetime.now(timezone.utc)

    def _safe(fn, default):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — a status page must never crash the brain
            logger.debug(f"hud: section failed ({type(e).__name__})")
            return default

    fallbacks = [m for m in (settings.llm_fallback_models or "").split(",") if m.strip()]
    return {
        "generated": now.isoformat(timespec="seconds"),
        "assistant": settings.assistant_name,
        "owner": settings.user_name,
        "presence": _safe(presence.current_line, ""),
        "objectives": _safe(lambda: _objectives(objectives), []),
        "working_on": _safe(lambda: _working_on(tasks), []),
        "awaiting_approval": _safe(lambda: _awaiting_approval(approvals), []),
        "systems": {
            "llm": settings.llm_primary_model,
            "fallbacks": len(fallbacks),
            "brain": "online",
        },
        # Live observability — closes the "no always-on latency/health surface" gap (Efficiency + Production).
        "performance": _safe(_performance, {}),
        "health": _safe(_health, []),
        "reliability": _safe(_reliability, []),
    }


def _performance() -> dict:
    """Turn count, LLM failovers, and recent LLM-route latency percentiles from the live metrics."""
    from afon.brain.metrics import METRICS

    snap = METRICS.snapshot()
    counters = snap.get("counters", {})
    route = snap.get("latency_ms", {}).get("llm_route_ms", {})
    return {
        "uptime_s": snap.get("uptime_s", 0.0),
        "turns": counters.get("turns", 0),
        "tool_calls": counters.get("tool_calls", 0),
        "tool_errors": counters.get("tool_errors", 0),
        "llm_p50_ms": route.get("p50", 0.0),
        "llm_p95_ms": route.get("p95", 0.0),
    }


def _health() -> list[dict]:
    """Component health as [{name, ok, detail}] — only the parts a status page can read synchronously.

    ponytail: the async ticker/vault probes live in health.check(); the HUD serves from a sync HTTP
    handler, so it reads the last on-disk probe result rather than re-probing per poll.
    """
    import json

    # Ask reliability where its log is; do NOT rebuild the path here (J3.6). Two independent
    # spellings of the same location is how the status page ends up serving nothing while the probe
    # writes happily somewhere else — and the `except` below swallows it, so the page just shows no
    # components rather than an error. The .jarvis -> .afon rename is exactly that shape of move.
    from afon.brain.reliability import _last_probe_path as probe_path

    out: list[dict] = []
    try:
        entries = json.loads(probe_path.read_text(encoding="utf-8"))
        for p in (entries[-1]["probes"] if entries else []):
            out.append({"name": p["name"], "ok": bool(p.get("ok")), "detail": p.get("err", "")})
    except Exception:  # noqa: BLE001 — no probe yet is fine, report nothing rather than crash
        pass
    return out


def _reliability() -> list[dict]:
    """Recently-flaky tools (worst first), learned from the audit trail — empty when all healthy."""
    from afon.brain.tool_reliability import reliability_summary

    return reliability_summary()


if __name__ == "__main__":  # tiny smoke against the real singletons
    import json

    print(json.dumps(hud_snapshot(), indent=2))
