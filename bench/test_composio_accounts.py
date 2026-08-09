"""Composio connected-accounts health check.

Lists every app account connected to your Composio project and its LIVE status (ACTIVE / INITIATED /
EXPIRED / FAILED), plus how many tools each toolkit exposes — using the Composio v3 API with the key in
``AFON_COMPOSIO_API_KEY`` (or ``COMPOSIO_API_KEY``).

    uv run python bench/test_composio_accounts.py

Read-only: nothing is modified. Exit 0 always (informational); non-ACTIVE accounts are flagged so you
know exactly which to reconnect in the Composio dashboard. No key set -> prints how to add one, exits 0.
"""

from __future__ import annotations

import os
import sys

import httpx

API = "https://backend.composio.dev/api/v3"


def _key() -> str | None:
    k = os.environ.get("AFON_COMPOSIO_API_KEY") or os.environ.get("COMPOSIO_API_KEY")
    if k:
        return k
    try:
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src"))
        from afon.config import settings
        return settings.composio_api_key
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    key = _key()
    if not key:
        print("No Composio key set. Put AFON_COMPOSIO_API_KEY=ak_... in .env, then re-run.")
        return 0
    h = {"x-api-key": key}
    try:
        accts = httpx.get(f"{API}/connected_accounts", headers=h,
                          params={"limit": 500}, timeout=30).json().get("items", [])
    except Exception as e:  # noqa: BLE001
        print(f"  x couldn't reach Composio ({type(e).__name__}: {e}) — check the key/network.")
        return 1

    tool_counts: dict[str, int] = {}
    try:
        for tk in httpx.get(f"{API}/toolkits", headers=h,
                            params={"limit": 500}, timeout=30).json().get("items", []):
            tool_counts[tk.get("slug")] = (tk.get("meta") or {}).get("tools_count")
    except Exception:  # noqa: BLE001 — tool counts are a bonus, not required
        pass

    print("=" * 66)
    print(" COMPOSIO — connected accounts")
    print("=" * 66)
    by_status: dict[str, int] = {}
    for a in sorted(accts, key=lambda x: x.get("toolkit", {}).get("slug", "")):
        slug = a.get("toolkit", {}).get("slug", "?")
        status = a.get("status", "?")
        reason = a.get("status_reason") or ""
        tools = tool_counts.get(slug)
        mark = "OK " if status == "ACTIVE" else "!! "
        tcol = f" — {tools} tools" if tools else ""
        rcol = f"  ({reason})" if reason and status != "ACTIVE" else ""
        print(f"  {mark} {slug:<20} {status}{tcol}{rcol}")
        by_status[status] = by_status.get(status, 0) + 1

    active = by_status.get("ACTIVE", 0)
    print(f"\n  {active}/{len(accts)} ACTIVE  ·  breakdown: {by_status}")
    not_active = sorted({a.get("toolkit", {}).get("slug") for a in accts if a.get("status") != "ACTIVE"})
    if not_active:
        print(f"  ! reconnect these in the Composio dashboard: {', '.join(not_active)}")
    print("\n=== done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
