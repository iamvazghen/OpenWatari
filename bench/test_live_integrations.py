"""Live integration smoke test: one green/red readout of every external service.

Unlike run_all_tests.py, this actually calls each configured provider with your real keys. Anything
unconfigured is reported as SKIP, not FAIL. Network/credential errors are FAIL.

Run:
    uv run python bench/test_live_integrations.py
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.config import settings  # noqa: E402

rows: list[tuple[str, str, str]] = []  # (status, name, detail)
LIVE_TIMEOUT_S = max(30, settings.http_timeout_seconds + 15)


def record(name: str, configured: bool, ok: bool, detail: str) -> None:
    status = "SKIP" if not configured else ("PASS" if ok else "FAIL")
    rows.append((status, name, detail.replace("\n", " ")[:90]))


def _ok(result: str) -> bool:
    """A tool result is 'live' if it didn't degrade or error."""
    low = result.lower()
    return "isn't configured" not in result and "couldn't" not in low and "rejected" not in low


async def run_check(
    name: str,
    configured: bool,
    call: Callable[[], Awaitable[str]],
    ok: Callable[[str], bool] = _ok,
    timeout_s: int = LIVE_TIMEOUT_S,
) -> None:
    try:
        result = await asyncio.wait_for(call(), timeout=timeout_s)
        record(name, configured, ok(result), result)
    except TimeoutError:
        record(name, configured, False, f"Timed out after {timeout_s}s")
    except Exception as e:  # noqa: BLE001
        record(name, configured, False, f"{type(e).__name__}: {e}")


async def cancel_leftovers() -> None:
    """Cancel any async tasks a live SDK left behind so the script can exit cleanly."""
    current = asyncio.current_task()
    pending = [task for task in asyncio.all_tasks() if task is not current and not task.done()]
    if not pending:
        return
    for task in pending:
        task.cancel()
    try:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=5)
    except TimeoutError:
        print(f"WARNING: {len(pending)} async task(s) did not finish cleanup within 5s")


async def main() -> int:
    from afon.brain.tools import (
        calendar,
        gmail,
        notify,
        notion,
        smarthome,
        telegram,
        vault,
        web,
    )

    await run_check(
        "Vault search",
        bool(settings.vault_path),
        lambda: vault.search_vault({"query": "project"}),
    )
    await run_check(
        "Tavily web_search",
        bool(settings.tavily_api_key),
        lambda: web.web_search({"query": "what is the capital of France"}),
        lambda r: "Paris" in r or "Answer" in r,
    )
    await run_check(
        "Jina scrape_url",
        True,
        lambda: web.scrape_url({"url": "example.com"}),
        lambda r: "Example Domain" in r or "example" in r.lower(),
    )
    await run_check(
        "Browserbase browse_web",
        bool(settings.browserbase_api_key and settings.browserbase_project_id),
        lambda: web.browse_web({"url": "example.com"}),
        lambda r: "Example Domain" in r,
        timeout_s=60,
    )
    await run_check(
        "Telegram read",
        bool(settings.telegram_api_id and settings.telegram_api_hash),
        lambda: telegram.check_telegram({"limit": 5}),
    )
    await run_check(
        "Gmail read_email",
        bool(settings.google_refresh_token),
        lambda: gmail.read_email({"query": "is:unread", "max": 3}),
    )
    await run_check(
        "Calendar list_events",
        bool(settings.google_refresh_token),
        lambda: calendar.list_events({}),
    )
    await run_check(
        "Notion search",
        bool(settings.notion_token),
        lambda: notion.notion_search({"query": "project"}),
    )
    await run_check(
        "Home Assistant",
        bool(settings.ha_url and settings.ha_token),
        lambda: smarthome.ha_state({}),
    )
    await run_check(
        "ntfy push",
        bool(settings.ntfy_topic),
        lambda: notify.send_push({"message": "Afon live-integration check ok", "title": "Afon"}),
        lambda r: "Pushed" in r,
    )

    print("=" * 72)
    print(" LIVE INTEGRATIONS")
    print("=" * 72)
    icon = {"PASS": "✓", "FAIL": "✗", "SKIP": "·"}
    for status, name, detail in rows:
        print(f"  {icon[status]} {status:4} {name:24} {detail}")
    npass = sum(1 for status, _, _ in rows if status == "PASS")
    nfail = sum(1 for status, _, _ in rows if status == "FAIL")
    nskip = sum(1 for status, _, _ in rows if status == "SKIP")
    print(f"\n  {npass} live, {nfail} failed, {nskip} not configured")

    await cancel_leftovers()
    return 1 if nfail else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
