"""Autonomous backlog worker (Phase 3.1 / recommendation 1.4).

A daily routine that turns Watari from a task *recorder* into a task *worker*: pull the owner's
overdue + undated-inbox Notion tasks and have the bounded ``TaskWorker`` attempt the SAFE work on each
(research / draft / summarise), then post the worker's result as a Notion comment on that task so the
owner sees progress next time they open it.

Safe by construction: the worker DEFERS every outward/destructive (confirm-gated) step — it never
sends, deletes, pushes, or runs shell unattended; those are listed back for the owner to approve. The
routine is opt-in (``JARVIS_BACKLOG_ENABLED``), capped per run, and fully graceful — no Notion, no
tasks DB, or no tasks just means a no-op. ``fetch``/``comment`` are injectable so the logic is
hermetically testable without Notion or a real LLM.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from loguru import logger

from jarvis.brain.worker import TaskWorker


async def attempt_backlog(
    llm: Any,
    registry: dict[str, Callable[[dict], Awaitable[str]]],
    worker_tools: list[dict[str, Any]],
    max_tasks: int = 2,
    max_steps: int = 6,   # room to VERIFY online + scrape the task's links + draft (4 was too tight)
    fetch: Callable[..., Awaitable[list[dict]]] | None = None,
    comment: Callable[[str, str], Awaitable[None]] | None = None,
) -> list[dict]:
    """Attempt the safe work on the top backlog tasks; return ``[{title, result, commented}]``.

    Each task gets its own fresh worker (no shared state). A worker error on one task is logged and
    skipped — one bad task never stops the pass. The Notion comment is the routine's sanctioned output
    (writing to the owner's own task page), so it's posted directly; genuinely outward actions stay
    deferred inside the worker.
    """
    if fetch is None:
        from jarvis.brain.tools.notion import fetch_backlog_tasks as fetch
    tasks = await fetch(limit=max_tasks)
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from jarvis.config import settings
    today = datetime.now(ZoneInfo(settings.user_tz)).strftime("%B %d, %Y")
    attempted: list[dict] = []
    for t in tasks:
        title = (t.get("title") or "(untitled)").strip()
        page_id = (t.get("id") or "").strip()
        # Read the task's OWN content first (links, buy-lists, prior notes) so the worker grounds its
        # work in what the owner already wrote — not just the title. The generic fluff the owner saw
        # ("wait for FIFA 26 to release") came from working off the bare title with STALE model knowledge
        # and never opening the task, which already held the reseller link + the player buy-list.
        body = ""
        if page_id:
            try:
                from jarvis.brain.tools.notion import notion_read_page
                b = await notion_read_page({"page_id": page_id})
                if b and "no readable text" not in b.lower() and "not configured" not in b.lower():
                    body = b
            except Exception:  # noqa: BLE001 — no body just means work off the title
                pass
        objective = (
            f"Today is {today}. Work on this task from the owner's backlog: '{title}'.\n"
            + (f"\nThe task's OWN notes — read and USE these; follow any link, honour any list:\n{body}\n"
               if body else "")
            + "\nGround your work in facts, not memory: before stating anything that changes over time "
            "(release dates, prices, availability, current events), VERIFY it with web_search across a "
            "couple of sources — your training knowledge is stale and must not be trusted for these. If "
            "the task contains a URL, scrape_url it and base your work on what it actually says. THEN do "
            "the safe research and drafting to move the task forward. Do NOT take any outward-facing or "
            "destructive action.\n"
            "Format of your final result (it is posted as a Notion comment the owner reads): LEAD with "
            "the deliverable itself — the findings, the draft, the numbers. No process narration ('I "
            "searched...', 'I will now...'), no restating the task, no filler. Complete every sentence. "
            "End with a short 'Sources:' line naming what you verified against."
        )
        worker = TaskWorker(llm, registry, worker_tools, max_steps=max_steps)
        try:
            result = await worker.run(objective)
        except Exception as e:  # noqa: BLE001 — one task must not break the whole pass
            logger.warning(f"backlog worker failed on '{title}': {type(e).__name__}")
            continue
        commented = False
        if page_id:
            try:
                if comment is not None:
                    await comment(page_id, result)
                else:
                    from jarvis.brain.tools.notion import notion_comment

                    await notion_comment({"page_id": page_id, "text": f"[Watari] {result}"})
                commented = True
            except Exception as e:  # noqa: BLE001 — a failed comment shouldn't lose the work
                logger.warning(f"backlog comment failed on '{title}': {type(e).__name__}")
        attempted.append({"title": title, "result": result, "commented": commented})
    if attempted:
        logger.info(f"backlog pass: attempted {len(attempted)} task(s)")
    return attempted
