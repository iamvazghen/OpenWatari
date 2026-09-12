"""20.F3 — every integration's timeout and retry policy is declared in one place.

The timeout was already central. The retry policy was not, because there wasn't one: every
integration got exactly one attempt, so a single dropped packet to the weather API reached the
owner as "I couldn't reach the weather service" — a sentence describing an outage, produced by a
hiccup. The opposite mistake is worse, which is why this is a table and not a blanket rule:
retrying a slow scrape turns a thirty-second wait into ninety, and retrying a 401 spends quota to
be told "no" again.

What this asserts:

  * the policy is data, not thirty numbers spread across modules, and every declared value is
    finite and bounded;
  * no tool module opens its own HTTP client, so the policy cannot be bypassed by accident;
  * a transient failure is retried exactly as declared, and then reported rather than invented;
  * an authentication failure is NOT retried, and a 429 IS — the one 4xx that means "later".

Hermetic: a fake transport. No network.

    uv run python bench/test_api_policy.py
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


async def main() -> None:
    import httpx

    from afon.brain.tools import base

    print("[1] the policy is one declared table, and every row is defensible")
    check("there is a stated default", base.DEFAULT_HTTP_POLICY.timeout > 0)
    check("the default retries at least once, so a dropped packet is not an outage",
          base.DEFAULT_HTTP_POLICY.retries >= 1)
    bad = [h for h, p in base.HTTP_POLICIES.items()
           if not (0 < p.timeout <= 120) or not (0 <= p.retries <= 3)]
    check("every declared timeout is finite and every retry count is bounded", bad == [], str(bad))
    check("every row says WHY, so the next person can argue with it",
          all(len(p.why) > 20 for p in base.HTTP_POLICIES.values()))
    # A slow renderer must not be retried: the retry pays the same long render again for the same
    # answer, and the owner is the one waiting.
    check("the slow scrapers are declared no-retry",
          base.HTTP_POLICIES["r.jina.ai"].retries == 0
          and base.HTTP_POLICIES["api.firecrawl.dev"].retries == 0)

    print("\n[2] a URL resolves to exactly one policy, and an unlisted host gets the default")
    check("an exact host matches", base.policy_for("https://r.jina.ai/x").timeout == 45.0)
    check("a subdomain matches its parent rule",
          base.policy_for("https://eu.api.telegram.org/bot/send").retries
          == base.HTTP_POLICIES["api.telegram.org"].retries)
    check("an unlisted host gets the stated default, never a guess",
          base.policy_for("https://example.invalid/x") is base.DEFAULT_HTTP_POLICY)
    check("a malformed URL still resolves rather than raising",
          base.policy_for("not a url") is base.DEFAULT_HTTP_POLICY)

    print("\n[3] no module opens its own client — the policy cannot be bypassed by accident")
    offenders: list[str] = []
    for py in sorted((ROOT / "src" / "afon").rglob("*.py")):
        if py.name == "base.py" and py.parent.name == "tools":
            continue
        src = py.read_text(encoding="utf-8", errors="replace")
        for pat in (r"httpx\.AsyncClient\(", r"httpx\.(get|post|put|patch|delete)\(",
                    r"^import requests", r"requests\.(get|post|put|patch|delete|Session)\("):
            if re.search(pat, src, re.M):
                offenders.append(f"{py.relative_to(ROOT)}: {pat}")
    # Out of scope, each for a stated reason:
    #   * llm.py and the edge are not integrations — they are the transport the assistant is made
    #     of, with their own failover rules (S02/S32);
    #   * a file that consults `policy_for` is using the table, which is the point of the rule.
    #     semantic.py is the one synchronous outbound call in the brain and reads its timeout from
    #     the table rather than carrying a number of its own.
    def exempt(o: str) -> bool:
        path = o.split(":")[0].replace("\\", "/")
        if path.endswith("afon/brain/llm.py") or "/edge/" in path:
            return True
        if path.endswith("afon/setup_wizard.py"):
            # The wizard imports nothing from brain/ or edge/ — test_layering enforces that,
            # because it has to run before either is configured. It cannot read the table, so its
            # one number is stated at the call site with that reason attached.
            return True
        return "policy_for(" in (ROOT / path).read_text(encoding="utf-8", errors="replace")

    offenders = [o for o in offenders if not exempt(o)]
    check("no tool module builds its own HTTP client", offenders == [], str(offenders[:4]))

    print("\n[4] a transient failure is retried exactly as declared, then reported")
    calls: list[str] = []

    def transport(fail_times: int, exc: Exception):
        state = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            state["n"] += 1
            if state["n"] <= fail_times:
                raise exc
            return httpx.Response(200, text="ok")

        return httpx.MockTransport(handler)

    original = base._client

    def fake_client(url: str = "", **kw):
        kw["transport"] = fake_client.transport
        return original(url, **kw)

    base._client = fake_client  # type: ignore[assignment]
    base._BACKOFF = (0.0, 0.0)  # type: ignore[assignment]
    try:
        # openweathermap declares 2 retries -> 3 attempts. Two failures then a success.
        calls.clear()
        fake_client.transport = transport(2, httpx.ConnectError("boom"))
        r = await base.http_get("https://api.openweathermap.org/data")
        check("a declared-retry host recovers from a transient failure", r.text == "ok")
        check("...in exactly the declared number of attempts", len(calls) == 3, str(len(calls)))

        # More failures than the policy allows: it must RAISE, so the caller reports a failure
        # rather than inventing an answer.
        calls.clear()
        fake_client.transport = transport(99, httpx.ConnectError("boom"))
        raised = False
        try:
            await base.http_get("https://api.openweathermap.org/data")
        except httpx.ConnectError:
            raised = True
        check("a host that is genuinely down raises rather than returning nothing", raised)
        check("...after the declared attempts and no more", len(calls) == 3, str(len(calls)))

        # A no-retry host tries once.
        calls.clear()
        fake_client.transport = transport(99, httpx.ConnectError("boom"))
        try:
            await base.http_get("https://r.jina.ai/https://example.com")
        except httpx.ConnectError:
            pass
        check("a no-retry host is attempted once, not three times", len(calls) == 1, str(len(calls)))

        print("\n[5] only what retrying can fix is retried")

        def status_transport(code: int):
            async def handler(request: httpx.Request) -> httpx.Response:
                calls.append(str(request.url))
                return httpx.Response(code, text="no")

            return httpx.MockTransport(handler)

        calls.clear()
        fake_client.transport = status_transport(401)
        try:
            await base.http_get("https://api.openweathermap.org/data")
        except httpx.HTTPStatusError:
            pass
        check("a bad key is not retried — it would spend quota to be told no again",
              len(calls) == 1, str(len(calls)))

        calls.clear()
        fake_client.transport = status_transport(429)
        try:
            await base.http_get("https://api.openweathermap.org/data")
        except httpx.HTTPStatusError:
            pass
        check("a 429 IS retried — the one 4xx that means 'later', not 'no'",
              len(calls) == 3, str(len(calls)))

        calls.clear()
        fake_client.transport = status_transport(503)
        try:
            await base.http_get("https://api.openweathermap.org/data")
        except httpx.HTTPStatusError:
            pass
        check("a 503 is retried", len(calls) == 3, str(len(calls)))

        calls.clear()
        fake_client.transport = status_transport(404)
        try:
            await base.http_get("https://api.openweathermap.org/data")
        except httpx.HTTPStatusError:
            pass
        check("a 404 is not retried — the page will not appear on the second ask",
              len(calls) == 1, str(len(calls)))
    finally:
        base._client = original  # type: ignore[assignment]

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
