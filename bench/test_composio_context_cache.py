"""J3.8 — the Composio context memo is keyed on its own return values, and that is wrong twice.

``_context()`` resolves the owner's Composio user id and the set of ACTIVE toolkit slugs, then
memoises them for the process lifetime. The memo test is::

    if _user_id is not None and _active_toolkits is not None:

so what gets remembered depends on what came back, not on whether the lookup WORKED. Two failures
fall out of that, in opposite directions:

  * **A transient failure becomes permanent.** ``_get()`` raises (network blip, 401 after a key
    rotation, Composio down), the ``except`` swallows it, and ``uid`` keeps the value from
    ``settings.composio_user_id``. Non-None uid + empty set is then stored as a legitimate answer:
    "you have no connected apps". Every later call short-circuits on that, so toolkit scoping is
    dead until the brain restarts — and nothing anywhere says so, because the failure was swallowed
    at the bottom.
  * **A successful lookup is not remembered.** When no user id is configured and the account list
    resolves empty, ``uid`` is None, the memo never engages, and every single
    ``composio_find_tools`` call re-hits ``/connected_accounts`` on the network — inside the turn,
    where the owner is waiting.

The rule that fixes both is one line of intent: **memoise on success, retry on failure**, tracked
by an explicit flag rather than inferred from the payload. A resolution that succeeded and found
nothing is an answer; a resolution that threw is not an answer at all.

Also asserted here: ``composio_catalog.refresh()`` checks configuration before reaching for the
network. Unconfigured, it used to fall through to ``_context()`` and log a warning on every daily
refresh — an alarm for a state that is not a fault.

Hermetic: ``_get`` is stubbed, no network, no credentials read.

    uv run python bench/test_composio_context_cache.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain import composio_catalog  # noqa: E402
from afon.brain.tools import composio as cx  # noqa: E402
from afon.config import settings  # noqa: E402

passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def _reset() -> None:
    """Clear the memo the way a fresh process would."""
    cx._user_id = None
    cx._active_toolkits = None
    if hasattr(cx, "_context_resolved"):
        cx._context_resolved = False


class _Calls:
    def __init__(self) -> None:
        self.n = 0


def _stub_ok(calls: _Calls, items: list[dict]):
    async def _get(_path, _params=None):
        calls.n += 1
        return {"items": items}
    return _get


def _stub_boom(calls: _Calls):
    async def _get(_path, _params=None):
        calls.n += 1
        raise RuntimeError("composio unreachable")
    return _get


async def main() -> None:
    saved_get, saved_uid = cx._get, settings.composio_user_id
    saved_key = settings.composio_api_key
    try:
        print("[1] a successful lookup is remembered — even when it finds nothing")
        # The expensive case: no configured user id, no connected accounts. The lookup SUCCEEDED
        # and the answer is "none"; re-asking the network every turn is pure latency.
        settings.composio_user_id = None
        _reset()
        calls = _Calls()
        cx._get = _stub_ok(calls, [])
        await cx._context()
        await cx._context()
        await cx._context()
        check(calls.n == 1, "an empty-but-successful resolution is fetched once, not per call",
              f"{calls.n} network calls for 3 lookups")

        print("\n[2] a successful lookup with accounts is remembered")
        settings.composio_user_id = None
        _reset()
        calls = _Calls()
        cx._get = _stub_ok(calls, [
            {"status": "ACTIVE", "user_id": "u-1", "toolkit": {"slug": "github"}},
            {"status": "INITIATED", "user_id": "u-1", "toolkit": {"slug": "slack"}},
        ])
        uid, kits = await cx._context()
        await cx._context()
        check(calls.n == 1, "resolved context is fetched once", f"{calls.n} calls")
        check(uid == "u-1", "user id resolved from the active account", str(uid))
        check(kits == {"github"}, "only ACTIVE toolkits count", str(kits))

        print("\n[3] a FAILED lookup is not remembered as an answer")
        # The dangerous one: a configured user id means uid is non-None even when the fetch threw,
        # so the old memo stored "you have no connected apps" permanently.
        settings.composio_user_id = "u-configured"
        _reset()
        calls = _Calls()
        cx._get = _stub_boom(calls)
        uid, kits = await cx._context()
        check(kits == set(), "a failed lookup degrades to an unscoped search (no crash)", str(kits))
        # …and the next call must try again rather than serve the failure.
        cx._get = _stub_ok(calls, [{"status": "ACTIVE", "user_id": "u-1",
                                    "toolkit": {"slug": "linear"}}])
        uid2, kits2 = await cx._context()
        check(calls.n == 2, "the next call retries instead of serving the swallowed failure",
              f"{calls.n} calls")
        check(kits2 == {"linear"}, "…and recovers the real toolkits once the API is back",
              str(kits2))

        print("\n[4] a recovered context stays memoised")
        before = calls.n
        await cx._context()
        check(calls.n == before, "no further fetch after a successful recovery")

        print("\n[5] refresh() does not reach for the network unconfigured")
        settings.composio_api_key = None
        _reset()
        calls = _Calls()
        cx._get = _stub_boom(calls)
        out = await composio_catalog.refresh()
        check(out == {}, "unconfigured refresh returns empty", str(out)[:80])
        check(calls.n == 0, "…without a network call, so an unset key is not an alarm",
              f"{calls.n} calls")
    finally:
        cx._get, settings.composio_user_id = saved_get, saved_uid
        settings.composio_api_key = saved_key
        _reset()

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
