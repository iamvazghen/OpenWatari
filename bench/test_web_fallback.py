"""Search/scrape provider fallback — Phase 4.5 (hermetic, no network).

Verifies the capability survives a provider outage: web_search chains Tavily -> Brave -> Jina (the
keyless tail is always present, so search works with NO keys), and scrape_url chains Jina -> Firecrawl.
We monkeypatch each provider fn to simulate outages and assert the chain fails over and still answers.

    uv run python bench/test_web_fallback.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> None:
    import afon.brain.tools.web as web
    from afon.brain.cache import CACHE
    from afon.config import settings

    async def boom(_q):
        raise RuntimeError("provider outage")

    print("[1] keyless tail: search works with NO keys (Jina always present)")
    settings.tavily_api_key = None
    settings.brave_api_key = None
    names = [n for n, _ in web._search_providers()]
    check("no keys -> only the keyless Jina provider", names == ["Jina"], str(names))

    settings.tavily_api_key = "t"
    settings.brave_api_key = "b"
    names = [n for n, _ in web._search_providers()]
    check("with keys -> Tavily, Brave, then Jina (in order)", names == ["Tavily", "Brave", "Jina"], str(names))

    print("\n[2] web_search fails over Tavily -> Brave -> Jina")
    used = []

    async def jina_ok(q):
        used.append("Jina")
        return f"Jina result for {q}"

    orig = (web._search_tavily, web._search_brave, web._search_jina)
    web._search_tavily, web._search_brave, web._search_jina = boom, boom, jina_ok
    try:
        CACHE._mem.clear() if hasattr(CACHE, "_mem") else None
        out = asyncio.run(web.web_search({"query": "rabbit farming tips xyz1"}))
    finally:
        web._search_tavily, web._search_brave, web._search_jina = orig
    check("first two providers down, third answers", "Jina result" in out, out)
    check("the failover actually reached Jina", used == ["Jina"], str(used))

    print("\n[3] scrape_url fails over Jina -> Firecrawl")
    settings.firecrawl_api_key = "f"
    sc_names = [n for n, _ in web._scrape_providers()]
    check("scrape chain is Jina then Firecrawl", sc_names == ["Jina", "Firecrawl"], str(sc_names))

    used2 = []

    async def fc_ok(u):
        used2.append("Firecrawl")
        return f"# Page\nscraped {u}"

    origs = (web._scrape_jina, web._scrape_firecrawl)
    web._scrape_jina, web._scrape_firecrawl = boom, fc_ok
    try:
        out2 = asyncio.run(web.scrape_url({"url": "example.com/xyz2"}))
    finally:
        web._scrape_jina, web._scrape_firecrawl = origs
    check("Jina down, Firecrawl scrapes", "scraped" in out2, out2)
    check("the failover actually reached Firecrawl", used2 == ["Firecrawl"], str(used2))

    print("\n[4] total outage degrades to a spoken note, never crashes")
    web._search_tavily, web._search_brave, web._search_jina = boom, boom, boom
    try:
        out3 = asyncio.run(web.web_search({"query": "anything at all zzz9"}))
    finally:
        web._search_tavily, web._search_brave, web._search_jina = orig
    check("all search providers down -> error note, no crash",
          isinstance(out3, str) and ("trouble" in out3.lower() or "couldn't" in out3.lower()
                                     or "error" in out3.lower() or "sir" in out3.lower()), out3)

    print("\n[5] read-only web tools are never confirm-gated; interactive browser IS (Phase 3.5)")
    from afon.brain.proactive import confirm_required
    check("scrape_url is not confirm-gated", confirm_required("scrape_url", {"url": "x"}) is False)
    check("web_search is not confirm-gated", confirm_required("web_search", {"query": "x"}) is False)
    check("browse_web (read-only fetch) is not confirm-gated",
          confirm_required("browse_web", {"url": "x"}) is False)
    # The interactive Playwright browser CAN log in / submit forms / spend, so it MUST stay gated —
    # the line that separates 'read the web' (free) from 'act on the web' (confirm).
    check("interactive browser IS confirm-gated", confirm_required("browser", {"url": "x"}) is True)

    settings.tavily_api_key = settings.brave_api_key = settings.firecrawl_api_key = None
    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
