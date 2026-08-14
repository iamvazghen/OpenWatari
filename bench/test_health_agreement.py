"""J3.6 — four answers to "what is broken right now", and nothing made them agree.

The surfaces, and what each is actually FOR (they are not duplicates, which is why the answer is
agreement rather than deletion):

  * `health.check()` / `health_signals()` — structural, cheap, runs on the proactive tick. Asks
    `validate_vault()`: is the path configured and present.
  * `reliability.health_probe()` — functional, runs on a schedule: actually searches the vault, calls
    the LLM. Catches a present-but-unreadable vault that the structural check calls fine.
  * `hud._health()` — the status page. NOT independent: it serves the last on-disk probe result,
    because it answers a synchronous HTTP handler and must not re-probe per poll.
  * `diagnose` — a different question entirely (what has ERRORED recently), read from the journal.

So the drift risk is between the first two, and it is not theoretical: they disagreed on what "the
vault is up" means, and the probe decided it by substring-matching English
(`"couldn't" not in r.lower()[:30]`). That breaks in both directions — reword the tool's failure
sentence and a dead vault reads healthy; return a legitimate "I couldn't find anything matching…"
from a healthy vault and the owner gets paged about a fault that does not exist.

What this asserts is the only guarantee that matters to the owner: **when a component is genuinely
broken, no surface calls it healthy.** Not that they use the same words, or run the same probe.

Hermetic — and it had to be MADE so: `health_probe()` probes the LLM with a real 1-token completion
through the live failover chain (deliberately: the LLM is the organ most worth probing for real).
Left alone this test cost two live model calls per run and would go red on a flaky uplink, reporting
a health-agreement bug that did not exist. The client is stubbed instead; the probe's own LLM check
is exercised by test_reliability.py.

    uv run python bench/test_health_agreement.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain import health, hud, reliability  # noqa: E402
from afon.brain.tools import vault as vault_tool  # noqa: E402
import afon.brain.context as ctx  # noqa: E402
import afon.brain.llm as llm_mod  # noqa: E402


class _StubMessage:
    content = "pong"
    tool_calls = None


async def _stub_complete(self, *_a, **_k):   # noqa: ANN001 — stands in for LLMClient.complete
    return _StubMessage()

passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def _probe(name: str, probes: list[dict]) -> dict:
    return next((p for p in probes if p["name"] == name), {})


async def main() -> None:
    saved = (ctx.validate_vault, vault_tool.search_vault, reliability._last_probe_path)
    saved_complete = llm_mod.LLMClient.complete
    tmp = Path(tempfile.mkdtemp()) / "health_probe.json"
    reliability._last_probe_path = tmp
    llm_mod.LLMClient.complete = _stub_complete
    try:
        print("[1] a HEALTHY vault: no surface invents a fault")
        ctx.validate_vault = lambda: (True, "vault ok")

        async def _found(_args):
            return "3 notes mention that, sir: …"

        vault_tool.search_vault = _found
        snap = await health.check()
        probes = await reliability.health_probe()
        check(snap["vault"]["ok"], "health.check(): vault ok")
        check(_probe("vault", probes).get("ok"), "health_probe(): vault ok", str(_probe("vault", probes)))
        check(not [s for s in await health.health_signals() if s.key == "health-vault"],
              "health_signals(): no vault alarm")

        print("\n[2] an EMPTY result is data, not a fault (the false page)")
        # A healthy vault that simply has no match used to be reported DOWN, because the probe read
        # the word "couldn't" in a perfectly good answer.
        async def _nothing(_args):
            return "I couldn't find anything matching that in your vault, sir."

        vault_tool.search_vault = _nothing
        probes = await reliability.health_probe()
        check(_probe("vault", probes).get("ok"),
              "health_probe(): 'nothing matched' is not a broken vault",
              str(_probe("vault", probes)))

        print("\n[3] a BROKEN vault: every surface says so, none says healthy")
        ctx.validate_vault = lambda: (False, "vault path unreadable")

        async def _boom(_args):
            raise OSError("vault unreadable")

        vault_tool.search_vault = _boom
        snap = await health.check()
        probes = await reliability.health_probe()
        sigs = await health.health_signals()
        check(not snap["vault"]["ok"], "health.check(): vault reported broken")
        check(not _probe("vault", probes).get("ok"), "health_probe(): vault reported broken")
        check(any(s.key == "health-vault" for s in sigs), "health_signals(): raises the alarm")
        # The HUD is downstream of the probe by design — but "by design" is worth an assertion,
        # since a status page showing green over a broken component is the whole failure mode.
        hud_rows = {r["name"]: r["ok"] for r in hud._health()}
        check(hud_rows.get("vault") is False,
              "hud._health(): serves the same verdict from the probe log", str(hud_rows))

        print("\n[4] the probe log the HUD reads is real, not a shape")
        entries = json.loads(tmp.read_text(encoding="utf-8"))
        check(bool(entries) and "probes" in entries[-1], "probe log written with entries")
        check({p["name"] for p in entries[-1]["probes"]} >= {"vault", "llm"},
              "…covering at least vault and llm", str(entries[-1]["probes"])[:120])
    finally:
        ctx.validate_vault, vault_tool.search_vault, reliability._last_probe_path = saved
        llm_mod.LLMClient.complete = saved_complete

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
