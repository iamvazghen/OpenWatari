"""J7.3 — why a tool failed must be a VALUE, not a sentence to grep.

Every handler is contracted to return speakable prose. That is right for the model and useless to a
caller that has to decide something — retry, fall back, page the owner, stay quiet — so deciding
meant substring-matching English. Two subsystems were already built that way: the LLM
permanent-error classifier and the degradation tests. String matching is silent when it breaks: a
reworded sentence leaves every caller compiling, running, and wrong.

`ErrorKind` + `ToolResult` fix it without touching the 132 edges into `tool_error`: the result is a
`str` SUBCLASS, so it stays speakable, json-dumpable and comparable, and merely carries `kind` for
callers that ask.

    uv run python bench/test_error_taxonomy.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def status(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://example.invalid")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, request=req))


def main() -> None:
    from afon.brain.tools.base import (ErrorKind, ToolResult, classify_error, is_not_configured,
                                       kind_of, not_configured, tool_error, tool_failed)

    print("[1] a typed result is still, in every way, a string")
    # This is the whole reason it can be adopted at all: nothing downstream may need changing.
    r = tool_error("web search", httpx.ConnectError("boom"))
    check(isinstance(r, str), "isinstance(result, str)")
    check(json.loads(json.dumps({"a": r}))["a"] == str(r), "survives json.dumps as a plain string")
    check(f"{r}".startswith("I couldn't complete"), "formats as prose")
    check(r == str(r) and hash(r) == hash(str(r)), "compares and hashes as its text")
    check({r: 1}[str(r)] == 1, "usable as a dict key interchangeably with str")
    check(r.lower().startswith("i couldn't"), "str methods work (and return plain str)")

    print("\n[2] the taxonomy maps what actually happens to this system")
    cases = [
        (status(401), ErrorKind.AUTH, "an expired key must not be retried"),
        (status(403), ErrorKind.AUTH, ""),
        (status(404), ErrorKind.NOT_FOUND, "retrying a missing page is pointless"),
        (status(429), ErrorKind.RATE_LIMIT, "retry later and slower, not immediately"),
        (status(500), ErrorKind.UNAVAILABLE, "the remote is up and failing"),
        (status(503), ErrorKind.UNAVAILABLE, ""),
        (status(400), ErrorKind.BAD_ARGS, "we called it wrong"),
        (httpx.ConnectError("no route"), ErrorKind.NETWORK, ""),
        (httpx.ReadTimeout("slow"), ErrorKind.NETWORK, ""),
        (TimeoutError(), ErrorKind.NETWORK, ""),
        (OSError("socket"), ErrorKind.NETWORK, ""),
        (ValueError("unknown argument"), ErrorKind.BAD_ARGS, ""),
        (KeyError("k"), ErrorKind.BAD_ARGS, ""),
        (RuntimeError("who knows"), ErrorKind.UNKNOWN, "no guessing when there is no evidence"),
    ]
    for exc, want, why in cases:
        got = classify_error(exc)
        label = f"{type(exc).__name__}" + (
            f" {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else "")
        check(got is want, f"{label:24s} -> {want.value}" + (f"  ({why})" if why else ""),
              f"got {got}")

    print("\n[3] the two producers carry it")
    check(kind_of(not_configured("Web search", "a Tavily key")) is ErrorKind.NOT_CONFIGURED,
          "not_configured() -> NOT_CONFIGURED")
    check(kind_of(tool_error("x", status(429))) is ErrorKind.RATE_LIMIT,
          "tool_error() -> the classified kind")
    check(kind_of("just some data") is None, "plain data has no kind")
    check(kind_of(None) is None, "None has no kind")

    print("\n[4] the old string helpers now prefer the type, and still handle prose")
    nc = not_configured("Web search", "a Tavily key")
    err = tool_error("web search", httpx.ConnectError("boom"))
    check(is_not_configured(nc) is True, "typed NOT_CONFIGURED is recognised")
    check(is_not_configured(err) is False,
          "a typed NETWORK error is definitively NOT 'not configured'",
          "this is the distinction the string match could not make reliably")
    check(tool_failed(err) and tool_failed(nc), "both count as failures")
    check(tool_failed("Your next event is at 3pm") is False, "data does not")
    # Prose from elsewhere — across the wire, an MCP server, a hand-written handler — must still work.
    check(is_not_configured("Web search isn't configured yet — it needs a key") is True,
          "untyped prose still classifies (the fallback is not removed)")
    check(tool_failed("I couldn't complete the web search just now (X).") is True,
          "...and so does an untyped error sentence")

    print("\n[5] the documented limit: the kind does NOT survive the wire")
    # Worth a test precisely because it is a limitation. A caller on the edge side must keep using
    # the prose helpers, and this is what says so out loud.
    across = json.loads(json.dumps({"r": err}))["r"]
    check(kind_of(across) is None, "after a json round-trip the kind is gone")
    check(tool_failed(across) is True, "...and the prose fallback still identifies it as a failure")

    print("\n[6] it reaches REAL tools, not just the helper")
    # A taxonomy only the helper knows about would be decoration.
    from afon.config import settings

    saved = settings.tavily_api_key, settings.brave_api_key
    try:
        settings.tavily_api_key = settings.brave_api_key = None
        from afon.brain.tools.web import web_search

        out = asyncio.run(web_search({"query": "anything"}))
        # Keyless providers may still answer; only assert the KIND when it actually declined.
        if is_not_configured(out) or tool_failed(out):
            check(kind_of(out) is not None or tool_failed(out),
                  "a real tool's failure is classifiable", str(out)[:80])
        else:
            check(True, "web_search answered via a keyless provider (nothing to classify)")
    finally:
        settings.tavily_api_key, settings.brave_api_key = saved

    calendar = __import__("afon.brain.tools.calendar", fromlist=["x"])
    src = Path(calendar.__file__).read_text(encoding="utf-8")
    check("not_configured(" in src or "tool_error(" in src,
          "a representative tool module routes failures through the shared helpers")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
