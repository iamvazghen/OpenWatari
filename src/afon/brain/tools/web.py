"""Web tools — search, scrape, and interactive browse.

Three complementary capabilities, each behind its own key (all degrade gracefully):

* ``web_search``  — Tavily API: fast LLM-grade web search with answer + sources. Best for
  "look up / what's the latest on X". (Other search scrapers can be slotted in the same way.)
* ``scrape_url``  — Jina Reader (r.jina.ai): fetch a single URL as clean Markdown (JS-rendered),
  **free and keyless**. Best for "open this page and tell me what it says" / "read me the headline".
* ``browse_web``  — Browserbase: a real headless Chrome for tasks that need interaction
  (click, fill, multi-step). Connects Playwright over CDP to a Browserbase session.

These are Afon's OWN web reach. Heavy multi-step domain research still goes to the fleet
(ispir) via delegate_to_fleet — these are for quick, self-served lookups.
"""

from __future__ import annotations

from loguru import logger

from afon.brain.cache import CACHE
from afon.brain.tools.base import clip, http_get, http_post, not_configured, tool_error
from afon.config import settings


# ---- web search: Tavily -> Brave -> Jina Search (keyless) --------------------------------
async def _search_tavily(query: str) -> str:
    r = await http_post(
        "https://api.tavily.com/search",
        json={"api_key": settings.tavily_api_key, "query": query,
              "search_depth": "basic", "include_answer": True, "max_results": 5},
    )
    data = r.json()
    parts: list[str] = []
    if data.get("answer"):
        parts.append(f"Answer: {data['answer']}")
    for res in (data.get("results") or [])[:5]:
        parts.append(f"- {res.get('title', '')}: {clip(res.get('content', ''), 200)} ({res.get('url', '')})")
    return "\n".join(parts)


async def _search_brave(query: str) -> str:
    r = await http_get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": 5},
        headers={"X-Subscription-Token": settings.brave_api_key, "Accept": "application/json"},
    )
    results = ((r.json() or {}).get("web") or {}).get("results") or []
    parts = [f"- {x.get('title', '')}: {clip(x.get('description', ''), 200)} ({x.get('url', '')})"
             for x in results[:5]]
    return "\n".join(parts)


async def _search_jina(query: str) -> str:
    # s.jina.ai/<query> — Jina's KEYLESS web search, returns results as clean Markdown. The always-on
    # last resort so search works even with no API keys configured at all.
    headers = {"X-Return-Format": "markdown"}
    if settings.jina_api_key:
        headers["Authorization"] = f"Bearer {settings.jina_api_key}"
    r = await http_get("https://s.jina.ai/" + query, headers=headers)
    return clip((r.text or "").strip(), 2500)


def _search_providers():
    """Ordered (name, fn) of the search providers that are actually available this deployment."""
    chain = []
    if settings.tavily_api_key:
        chain.append(("Tavily", _search_tavily))
    if settings.brave_api_key:
        chain.append(("Brave", _search_brave))
    chain.append(("Jina", _search_jina))   # keyless — always present
    return chain


async def _search_chain(query: str) -> str:
    last_err: Exception | None = None
    for name, fn in _search_providers():
        try:
            out = (await fn(query) or "").strip()
            if out:
                return out
            logger.info(f"web_search via {name} returned nothing; trying next provider")
        except Exception as e:  # noqa: BLE001 — provider down/outage: fail over to the next
            last_err = e
            logger.warning(f"web_search provider {name} failed ({type(e).__name__}); trying next")
    if last_err:
        raise last_err
    return f"No web results for '{query}', sir."


async def web_search(args: dict) -> str:
    query = (args.get("query") or "").strip()
    if not query:
        return "What should I search the web for, sir?"
    try:
        # L4 cache: a repeated search inside the TTL window returns instantly (near-zero TTFW).
        return await CACHE.cached(
            "web_search", key=query.lower(), ttl=600, factory=lambda: _search_chain(query)
        )
    except Exception as e:  # noqa: BLE001
        return tool_error("web search", e)


# ---- scrape: Jina Reader (keyless) -> Firecrawl ------------------------------------------
async def _scrape_jina(url: str) -> str:
    # Jina Reader (r.jina.ai): GET the reader origin — fetches, renders JS, returns clean Markdown.
    # Free + keyless; an optional AFON_JINA_API_KEY only raises rate limits.
    headers = {"X-Return-Format": "markdown"}
    if settings.jina_api_key:
        headers["Authorization"] = f"Bearer {settings.jina_api_key}"
    r = await http_get("https://r.jina.ai/" + url, headers=headers)
    return (r.text or "").strip()


async def _scrape_firecrawl(url: str) -> str:
    r = await http_post(
        "https://api.firecrawl.dev/v1/scrape",
        headers={"Authorization": f"Bearer {settings.firecrawl_api_key}"},
        json={"url": url, "formats": ["markdown"]},
    )
    return (((r.json() or {}).get("data") or {}).get("markdown") or "").strip()


# ---- 05.F3: is this the page, or the thing standing in front of the page? -------------------
# A scrape that returns *nothing* was already reported honestly. The dangerous case is the one that
# returns *something*: a Cloudflare interstitial, a paywall, a cookie wall, or a JS shell holding
# only the headline. Those read as content, so the model summarised the obstruction and the owner
# got a confident answer assembled from a title and a subscribe button. He cannot tell that apart
# from a real summary, which makes it worse than an error.

#: Below this, there is no article here whatever the page says.
_MIN_BODY = 400

#: Phrases only count as an obstruction inside a short body. An article ABOUT Cloudflare or about
#: paywalls contains every one of these words, and flagging it would train him to ignore the
#: warning — which costs more than the warning is worth.
_BLOCK_WINDOW = 1500

_BLOCK_PHRASES = (
    ("enable javascript", "the page needs JavaScript and none of my readers ran it"),
    ("javascript is disabled", "the page needs JavaScript and none of my readers ran it"),
    ("checking your browser", "it stopped at a bot check"),
    ("verify you are human", "it stopped at a bot check"),
    ("are you a robot", "it stopped at a bot check"),
    ("captcha", "it stopped at a bot check"),
    ("access denied", "the site refused the request"),
    ("403 forbidden", "the site refused the request"),
    ("429 too many requests", "the site is rate-limiting me"),
    ("subscribe to continue", "it is behind a paywall"),
    ("subscribers only", "it is behind a paywall"),
    ("subscription required", "it is behind a paywall"),
    ("to continue reading", "it is behind a paywall"),
    ("create a free account", "it wants an account first"),
    ("sign in to read", "it wants an account first"),
    ("log in to continue", "it wants an account first"),
    ("accept cookies", "it stopped at a cookie wall"),
    ("cookie preferences", "it stopped at a cookie wall"),
)


def obstructed(text: str) -> str:
    """Why this scrape is not the page's content, or "" if it looks like the real thing."""
    body = (text or "").strip()
    if not body:
        return "nothing came back at all"
    # Name the wall before measuring it. "It is behind a paywall" tells him whether to try another
    # source or reach for his subscription; "only 244 characters came back" tells him nothing he
    # can act on, and both sentences describe the same page.
    if len(body) <= _BLOCK_WINDOW:
        low = body.lower()
        for phrase, why in _BLOCK_PHRASES:
            if phrase in low:
                return why
    if len(body) < _MIN_BODY:
        return f"only {len(body)} characters came back, which is not an article"
    return ""


def _scrape_providers():
    chain = [("Jina", _scrape_jina)]          # keyless primary
    if settings.firecrawl_api_key:
        chain.append(("Firecrawl", _scrape_firecrawl))
    return chain


async def _scrape_chain(url: str) -> str:
    """The best body any provider returned. Obstructed bodies are kept only as a last resort, so
    the caller can say WHAT stood in the way rather than just that it failed."""
    last_err: Exception | None = None
    blocked = ""
    for name, fn in _scrape_providers():
        try:
            out = (await fn(url) or "").strip()
            why = obstructed(out)
            if not why:
                return out
            # An obstruction is worth another provider: Firecrawl renders pages Jina will not.
            logger.info(f"scrape via {name} looks obstructed ({why}); trying next provider")
            if len(out) > len(blocked):
                blocked = out
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning(f"scrape provider {name} failed ({type(e).__name__}); trying next")
    if blocked:
        return blocked
    if last_err:
        raise last_err
    return ""


async def scrape_url(args: dict) -> str:
    url = (args.get("url") or "").strip()
    if not url:
        return "Which page should I open, sir?"
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        # Cache briefly so "read me that page" repeated in one session is instant.
        md = await CACHE.cached("scrape", key=url, ttl=300, factory=lambda: _scrape_chain(url))
        # Re-derived rather than cached alongside the body: `obstructed` is pure, so one cached
        # string stays the single thing to invalidate.
        why = obstructed(md)
        if why:
            # The obstruction text is deliberately NOT returned. Handing back a subscribe button
            # and a headline is an invitation to summarise them, and a summary of a paywall is
            # indistinguishable to the owner from a summary of the article behind it.
            return (f"I couldn't read {url}, sir — {why}. I'd rather tell you that than summarise "
                    "the wall in front of it.")
        return clip(md, 3500)
    except Exception as e:  # noqa: BLE001
        return tool_error("page scrape", e)


async def browse_web(args: dict) -> str:
    """Interactive headless browse via Browserbase (Playwright over CDP).

    Needs the Browserbase key+project AND the optional 'browse' extra (Playwright). For a
    plain 'read this page' use scrape_url instead — this is for navigation/extraction tasks.
    """
    url = (args.get("url") or "").strip()
    task = (args.get("task") or "").strip()
    if not url:
        return "Give me a URL to browse, sir."
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if not (settings.browserbase_api_key and settings.browserbase_project_id):
        return not_configured(
            "the live browser",
            "a Browserbase API key + project id (AFON_BROWSERBASE_API_KEY / _PROJECT_ID)",
        )
    try:
        from playwright.async_api import async_playwright  # type: ignore
    except ImportError:
        return not_configured("the live browser", "the Playwright extra (uv sync --extra browse)")
    try:
        # Create a Browserbase session, then drive it with Playwright over CDP.
        sess = await http_post(
            "https://api.browserbase.com/v1/sessions",
            headers={"X-BB-API-Key": settings.browserbase_api_key, "Content-Type": "application/json"},
            json={"projectId": settings.browserbase_project_id},
        )
        connect_url = sess.json().get("connectUrl")
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(connect_url)
            ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            title = await page.title()
            text = await page.inner_text("body")
            await browser.close()
        note = f" (task: {task})" if task else ""
        return f"Browsed {url}{note}. Title: {title}. Content: {clip(text, 2500)}"
    except Exception as e:  # noqa: BLE001
        return tool_error("live browse", e)


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current information (news, facts, how-tos, prices) and get "
                "a synthesized answer with sources. Use for anything live or beyond your own "
                "knowledge that doesn't need deep multi-step research."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "The search query."}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scrape_url",
            "description": (
                "Fetch a single web page and return its main text as clean Markdown. Use for "
                "'open <site> and tell me the headline / what it says'."
            ),
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "The page URL."}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browse_web",
            "description": (
                "Open a real headless browser to navigate and extract from a page that needs "
                "interaction or JavaScript. Use only when scrape_url isn't enough."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to open."},
                    "task": {"type": "string", "description": "Optional: what to do/extract there."},
                },
                "required": ["url"],
            },
        },
    },
]

HANDLERS = {"web_search": web_search, "scrape_url": scrape_url, "browse_web": browse_web}
