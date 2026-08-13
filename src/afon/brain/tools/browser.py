"""Local interactive browser — a real, visible Chromium Afon fully drives.

Unlike ``web.browse_web`` (cloud Browserbase, for headless one-off extraction), this is a
**persistent, visible** browser on the owner's own screen: Afon opens windows/tabs, clicks
links and buttons, fills forms, and — when the owner asks him to log in — types the email and
password into the page. The profile is persistent, so logins stick between sessions.

One ``browser`` tool with an ``action`` so the LLM has a single clear verb:
  open · click · fill · type · press · read · screenshot · new_tab · back · close

Ops are FORWARDED to the laptop (pc_agent) whenever it's connected, so "visible, on his screen, with
his logins" stays true now that the brain lives on the VPS; they run in-process only when the brain
itself is on the laptop.

Needs the optional Playwright extra (``uv sync --extra browse`` then ``playwright install
chromium``). If it's missing the tool says so instead of crashing.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from loguru import logger

from afon.brain.tools.base import clip, missing_arg, not_configured, tool_error
from afon.config import settings

# browser.py lives at src/afon/brain/tools/ — repo root is 4 parents up (one deeper than
# the brain/ modules, which use parents[3]).
_REPO_ROOT = Path(__file__).resolve().parents[4]


class _Browser:
    """Singleton holding the live Playwright browser + current page across tool calls."""

    def __init__(self) -> None:
        self._pw = None
        self._ctx = None
        self._page = None
        self._lock = asyncio.Lock()

    async def _ensure(self):
        async with self._lock:
            if self._ctx is not None:
                return
            from playwright.async_api import async_playwright  # type: ignore

            self._pw = await async_playwright().start()
            profile = settings.browser_profile_dir or str(_REPO_ROOT / ".afon-browser")
            self._ctx = await self._pw.chromium.launch_persistent_context(
                profile, headless=settings.browser_headless,
                # autoplay-policy lets YouTube/music start playing without a manual click.
                args=["--start-maximized", "--autoplay-policy=no-user-gesture-required"],
                no_viewport=True,
            )
            self._ctx.set_default_timeout(settings.browser_nav_timeout_ms)
            self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()
            logger.info(f"browser: launched (headless={settings.browser_headless}, profile={profile})")

    @property
    def page(self):
        return self._page

    async def new_tab(self):
        self._page = await self._ctx.new_page()
        return self._page

    async def close(self):
        async with self._lock:
            if self._ctx:
                await self._ctx.close()
            if self._pw:
                await self._pw.stop()
            self._pw = self._ctx = self._page = None


_BROWSER = _Browser()


async def _browser_local(args: dict) -> str:
    timeout_s = 25.0
    try:
        return await asyncio.wait_for(_browser_action(args), timeout=timeout_s)
    except asyncio.TimeoutError:
        return (
            "The browser action timed out after 25 seconds, sir. I stopped trying so I don't get "
            "stuck in a loop. You may need to do that one manually or give me a simpler browser step."
        )


async def browser(args: dict) -> str:
    """Drive the browser on the LAPTOP when one is connected, else on this host.

    The whole point of this tool (vs headless ``browse_web``) is a browser the owner can SEE and whose
    logins persist in his own profile. Since the brain moved to the VPS, running it brain-side put it
    on a headless server he can't see, with a different cookie jar — so it now takes the same PC_LINK
    route as the file/process tools. Falls through to local when the brain runs on the laptop itself."""
    from afon.brain.tools.system import _dispatch

    return await _dispatch("browser", args, _browser_local)


async def _browser_action(args: dict) -> str:
    if not settings.browser_tools_enabled:
        return "The browser is disabled, sir (AFON_BROWSER_TOOLS_ENABLED)."
    action = (args.get("action") or "").strip().lower()
    try:
        import playwright  # noqa: F401
    except ImportError:
        return not_configured(
            "the live browser",
            "the Playwright extra (uv sync --extra browse, then 'playwright install chromium')",
        )
    try:
        if action != "close":
            _neutralize_speechbrain_lazy_modules()
            await _BROWSER._ensure()
        page = _BROWSER.page

        if action == "open":
            url = (args.get("url") or "").strip()
            if not url:
                return missing_arg("browser", args, "url", "action", "query", "text",
                                   ask="Which URL, sir?")
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            await page.goto(url, wait_until="domcontentloaded")
            title = await page.title()
            # T1b: open always returns a screenshot path so the LLM (and the owner) can verify
            # the page actually loaded (vs 404 / login wall / consent screen). Cheap: one PNG.
            shot = _REPO_ROOT / "browser-shot.png"
            try:
                await page.screenshot(path=str(shot), full_page=False)
                shot_text = f", screenshot at {shot}"
            except Exception:
                shot_text = ""
            return f"Opened {url} — '{title}'{shot_text}, sir."

        if action == "new_tab":
            page = await _BROWSER.new_tab()
            url = (args.get("url") or "").strip()
            if url:
                if not url.startswith(("http://", "https://")):
                    url = "https://" + url
                await page.goto(url, wait_until="domcontentloaded")
                return f"Opened a new tab at {url}, sir."
            return "Opened a new tab, sir."

        if action == "click":
            target = _locator(page, args)
            if target is None:
                return "Tell me what to click, sir — a button/link text or a CSS selector."
            try:
                await target.click(timeout=8000)
            except Exception:  # noqa: BLE001
                return (f"I couldn't find '{args.get('text') or args.get('selector')}' to click, "
                        "sir — the page may not have it.")
            return f"Clicked {args.get('text') or args.get('selector')}, sir."

        if action == "fill":
            sel = (args.get("selector") or "").strip()
            value = args.get("value") or ""
            if not sel:
                return "Which field should I fill, sir? Give me a selector."
            await page.fill(sel, value)
            shown = "•••" if args.get("secret") else clip(value, 40)
            return f"Filled {sel} with {shown}, sir."

        if action == "type":
            text = args.get("text") or args.get("value") or ""
            sel = (args.get("selector") or "").strip()
            if sel:
                await page.click(sel)
            await page.keyboard.type(text)
            return "Typed that in, sir."

        if action == "press":
            key = (args.get("key") or "Enter").strip()
            await page.keyboard.press(key)
            return f"Pressed {key}, sir."

        if action == "back":
            await page.go_back()
            return f"Went back — '{await page.title()}', sir."

        if action == "tabs":
            # T1d: tab awareness — list all open tabs with index + title + url so the owner can
            # pick one by saying its title (or refer to it). Stale entries (page closed by another
            # process) are filtered.
            pages = _BROWSER._ctx.pages
            out = []
            for i, p in enumerate(pages):
                try:
                    title = await p.title()
                    url = p.url
                except Exception:
                    continue
                marker = "►" if p is _BROWSER._page else " "
                out.append(f"  {marker} {i}. {title} — {url}")
            if not out:
                return "No tabs open, sir."
            return f"{len(out)} tab(s) open:\n" + "\n".join(out)

        if action == "switch_tab":
            # Switch the singleton `_page` to another tab (by index, title substring, or url).
            pages = _BROWSER._ctx.pages
            if not pages:
                return "No tabs to switch to, sir."
            arg = (args.get("index") or args.get("title") or args.get("url") or "").strip()
            pick = None
            if isinstance(arg, int) or (arg.isdigit() if arg else False):
                idx = int(arg)
                if 0 <= idx < len(pages):
                    pick = pages[idx]
            elif arg:
                for p in pages:
                    if arg.lower() in (await p.title()).lower() or arg.lower() in p.url.lower():
                        pick = p
                        break
            if pick is None:
                return f"I couldn't find a tab matching '{arg}', sir. Try 'tabs' to see them."
            _BROWSER._page = pick
            return f"Switched to '{await pick.title()}', sir."

        if action == "close_tab":
            # Close a tab by index/title/url; if it was the current page, fall back to another tab.
            pages = _BROWSER._ctx.pages
            if not pages:
                return "No tabs to close, sir."
            arg = (args.get("index") or args.get("title") or args.get("url") or "").strip()
            target = None
            if arg.isdigit() and 0 <= int(arg) < len(pages):
                target = pages[int(arg)]
            elif arg:
                for p in pages:
                    if arg.lower() in (await p.title()).lower() or arg.lower() in p.url.lower():
                        target = p
                        break
            if target is None:
                target = _BROWSER._page
                if len(pages) > 1:
                    return "Tell me which tab, sir (by index, title or url), or say 'close all except this'."
            await target.close()
            # If we closed the active page, repoint to the first remaining tab.
            if _BROWSER._page is target:
                remaining = _BROWSER._ctx.pages
                _BROWSER._page = remaining[0] if remaining else None
            return f"Closed '{await target.title() if not target.is_closed() else target.title() if False else 'tab'}', sir."

        if action == "close_all_other_tabs":
            # Close every tab except the current one (the LLM asks "close everything but this").
            current = _BROWSER._page
            n = 0
            for p in list(_BROWSER._ctx.pages):
                if p is not current:
                    try:
                        await p.close()
                        n += 1
                    except Exception:
                        pass
            return f"Closed {n} other tab(s), sir."

        if action == "read":
            title = await page.title()
            body = await page.inner_text("body")
            return f"{title}\n{clip(body, 3000)}"

        if action == "screenshot":
            path = (args.get("path") or str(_REPO_ROOT / "browser-shot.png")).strip()
            await page.screenshot(path=path, full_page=False)
            return f"Saved a screenshot to {path}, sir."

        if action == "close":
            await _BROWSER.close()
            return "Closed the browser, sir."

        return f"I don't know the browser action '{action}', sir."
    except Exception as e:  # noqa: BLE001
        _neutralize_speechbrain_lazy_modules()
        return tool_error("browser", e)


def _neutralize_speechbrain_lazy_modules() -> None:
    """Avoid Playwright error reporting tripping over SpeechBrain's optional lazy k2 module.

    Playwright calls inspect.stack() on API errors. inspect.getmodule() scans sys.modules and calls
    hasattr(module, "__file__"); SpeechBrain's LazyModule for k2 raises ImportError there if k2 is
    not installed. Giving that lazy module a harmless __file__ prevents an unrelated optional
    SpeechBrain dependency from masking the real browser error.

    The placeholder MUST be non-empty. It was `""` and that did not fix the hazard, it only changed
    its shape: `inspect.getfile()` does `if getattr(object, "__file__", None)`, and `""` is falsy,
    so the scan went on to raise `TypeError: <module …> is a built-in module` instead of the
    original ImportError — still masking the real browser error. Measured:

        unpatched (LazyModule)  -> ImportError: k2 missing
        patched with ""         -> TypeError: … is a built-in module
        patched with "<lazy>"   -> clean

    It went unnoticed for two reasons worth remembering: both call sites are inside
    `except Exception`, so the two exception types are indistinguishable downstream; and
    `inspect.getmodule` short-circuits on its `modulesbyfile` cache, so a warm process often skips
    the scan entirely and the failure looks intermittent. See bench/test_browser_speechbrain_guard.py.
    """
    placeholder = "<speechbrain lazy module>"
    for name, mod in list(sys.modules.items()):
        if not name.startswith("speechbrain.integrations.k2_fsa") or mod is None:
            continue
        try:
            object.__setattr__(mod, "__file__", placeholder)
        except Exception:  # noqa: BLE001 — a module that refuses the write is no worse than before
            try:
                setattr(mod, "__file__", placeholder)
            except Exception:  # noqa: BLE001
                pass


def _locator(page, args: dict):
    text = (args.get("text") or "").strip()
    sel = (args.get("selector") or "").strip()
    if sel:
        return page.locator(sel).first
    if text:
        # Match a link or button (or any element) by visible text.
        return page.get_by_text(text, exact=False).first
    return None


SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "browser",
            "description": (
                "Drive a real visible web browser on the owner's screen. Actions: open, "
                "new_tab, click (by visible text or CSS selector), fill (form field by "
                "selector), type (keystrokes), press (e.g. Enter), back, tabs (list all open "
                "tabs), switch_tab (by index/title/url), close_tab, close_all_other_tabs, "
                "read (page text), screenshot. The 'open' action auto-takes a screenshot so you "
                "can verify the page actually loaded (vs 404 / login wall / consent screen). "
                "For simply OPENING a site or search (e.g. 'open YouTube') with no "
                "clicking/typing, use open_url instead -- it's faster and uses his normal "
                "browser. Confirm before submitting anything that sends data or money."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["open", "click", "fill", "type", "press", "read",
                                 "screenshot", "new_tab", "back", "close", "tabs", "switch_tab", "close_tab", "close_all_other_tabs"],
                    },
                    "url": {"type": "string", "description": "For open/new_tab."},
                    "selector": {"type": "string", "description": "CSS selector (fill/click/type)."},
                    "text": {"type": "string", "description": "Visible text to click."},
                    "value": {"type": "string", "description": "Value to fill into a field."},
                    "secret": {"type": "boolean", "description": "Set true for passwords (won't be echoed)."},
                    "key": {"type": "string", "description": "Key to press, e.g. 'Enter'."},
                    "path": {"type": "string", "description": "Screenshot file path."},
                },
                "required": ["action"],
            },
        },
    },
]

HANDLERS = {"browser": browser}

# Executed by the laptop's pc_agent when the brain forwards a browser op (see browser() above).
LOCAL_HANDLERS = {"browser": _browser_local}
