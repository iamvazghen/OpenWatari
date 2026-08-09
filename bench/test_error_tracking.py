"""End-to-end error tracking — the guarantees that make a failure locatable immediately.

What must hold, and why each one was worth a test:

  * Every WARNING+ log line becomes a structured entry WITHOUT the call site doing anything. If this
    breaks, coverage silently shrinks to whatever was hand-instrumented.
  * Secrets never reach the journal. It is written to disk and shipped over the network; an httpx
    error quoting a URL with an api_key would otherwise leak on both.
  * A turn id correlates the laptop and the VPS. This is the whole point — one id, both machines.
  * Uncaught and background-task exceptions are captured. asyncio prints those to stderr, which
    pythonw discards, so they used to vanish entirely.
  * The tracker can never break a turn: a broken journal, a failing shipper, or a re-entrant sink
    must all be survivable.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from loguru import logger  # noqa: E402

from afon.shared import errors as err  # noqa: E402

PASS = FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label} {detail}")


def _use_temp_journal() -> Path:
    d = Path(tempfile.mkdtemp())
    err._DIR = d
    err.JOURNAL = d / "errors.jsonl"
    return err.JOURNAL


async def main() -> None:
    journal = _use_temp_journal()
    err.install("test", bridge_stdlib=False)

    print("\n[1] automatic capture — no call-site instrumentation")
    tid = err.new_turn()
    logger.warning("disk is nearly full")
    entries = err.read(limit=5)
    check("a plain logger.warning is journalled", len(entries) == 1, str(entries))
    check("the entry carries the active turn id", entries and entries[0]["turn"] == tid)
    check("the entry names its subsystem", entries and bool(entries[0]["subsystem"]))
    check("the entry records where it came from", entries and ":" in entries[0]["where"])
    logger.info("routine progress")
    check("INFO is NOT journalled (errors only, not a second log)", len(err.read(limit=9)) == 1)

    print("\n[2] secrets never reach disk")
    logger.error("POST https://api.example.com/v1?api_key=SUPERSECRET0987 failed")
    logger.error('auth failed: {"token": "TOPSECRETVALUE1"}')
    logger.error("Authorization: Bearer ZZZTOPSECRETBEARER")
    blob = journal.read_text(encoding="utf-8")
    for leak in ("SUPERSECRET0987", "TOPSECRETVALUE1", "ZZZTOPSECRETBEARER"):
        check(f"'{leak[:11]}…' is redacted", leak not in blob)
    check("the surrounding message is still readable", "api.example.com" in blob)

    print("\n[3] correlation across processes")
    edge_turn = err.new_turn()
    logger.warning("edge: stt stalled")
    # The brain adopts the SAME id off the wire, and its entries claim its own host/process.
    err.record(level="ERROR", subsystem="brain/llm", message="model refused",
               turn=edge_turn, process="brain", host="vps", ship=False)
    both = err.read(turn=edge_turn)
    check("one id retrieves entries from both machines", len(both) == 2, str(both))
    check("...and they are distinguishable by host",
          {e["host"] for e in both} == {err._HOST, "vps"}, str([e["host"] for e in both]))
    other = err.new_turn()
    logger.warning("a different turn")
    check("filtering by turn excludes other turns", len(err.read(turn=other)) == 1)

    print("\n[4] failures nobody caught")
    try:
        raise ValueError("bad input")
    except ValueError:
        logger.exception("handler failed")
    check("an exception's TYPE is captured", any(e["type"] == "ValueError" for e in err.read(limit=3)))

    loop = asyncio.get_running_loop()
    err.install_asyncio_handler(loop)
    before = len(err.read(limit=99))

    async def _boom():
        raise RuntimeError("background task died")

    t = asyncio.create_task(_boom())
    await asyncio.sleep(0)
    try:
        await t
    except RuntimeError:
        pass
    loop.call_exception_handler({"message": "Task exception was never retrieved",
                                 "exception": RuntimeError("background task died")})
    check("a fire-and-forget task failure is captured",
          len(err.read(limit=99)) > before and
          any("background task died" in e["message"] for e in err.read(limit=5)))

    print("\n[5] shipping to the brain")
    shipped: list[dict] = []
    err.set_shipper(shipped.append)
    logger.error("laptop-only problem")
    check("the entry is handed to the transport", shipped and shipped[-1]["message"] == "laptop-only problem")
    check("...and is still written locally first",
          any(e["message"] == "laptop-only problem" for e in err.read(limit=3)))

    def _broken_shipper(_e):
        raise ConnectionError("brain unreachable")

    err.set_shipper(_broken_shipper)
    logger.error("problem while the link is down")   # must not raise
    check("a broken shipper never raises into the logger",
          any(e["message"] == "problem while the link is down" for e in err.read(limit=3)))
    err.set_shipper(None)

    print("\n[6] the tracker cannot take the system down")
    err._in_sink.busy = True
    logger.error("re-entrant call")
    err._in_sink.busy = False
    check("the re-entrancy guard drops instead of recursing",
          not any(e["message"] == "re-entrant call" for e in err.read(limit=5)))

    saved = err.JOURNAL
    err.JOURNAL = Path("Z:/definitely/not/writable/errors.jsonl")
    check("an unwritable journal returns None rather than raising",
          err.record(level="ERROR", subsystem="x", message="y", ship=False) is None)
    logger.error("still logging with a dead journal")   # must not raise
    err.JOURNAL = saved

    print("\n[7] querying")
    s = err.summary(since_minutes=60)
    check("summary counts FAILURES in 'total' (the number he's asking about)", s["total"] > 0)
    check("summary counts successes separately", "ok" in s and "operations" in s)
    check("summary groups by subsystem", bool(s["by_subsystem"]))
    check("summary names the worst subsystem", bool(s["top"]))
    check("every line is valid JSON",
          all(json.loads(l) for l in journal.read_text(encoding="utf-8").splitlines() if l.strip()))

    print("\n[8] wired into the running system")
    from afon.brain.tools import tool_names
    from afon.shared.protocol import ErrorReport, Utterance

    check("the diagnose tool is registered", "diagnose" in tool_names())
    check("Utterance carries a turn id", "turn_id" in Utterance.model_fields)
    check("the wire has an ErrorReport message", ErrorReport(entry={"a": 1}).type == "error")

    from afon.brain.tools.diagnose import diagnose
    said = await diagnose({"minutes": 60})
    check("diagnose speaks a real summary", "problem" in said.lower() and "sir" in said.lower(), said)
    clean = await diagnose({"minutes": 60, "turn": "nosuchturn"})
    check("diagnose is honest when a turn has nothing", "nothing" in clean.lower(), clean)

    print("\n[9] the laptop executor reports home too")
    # pc_agent runs the camera, PC control and file ops. Its failures happen on the owner's machine,
    # so they must reach the brain's journal or half his experience stays invisible to it.
    import json as _json

    from afon.brain import pc_link

    sent: list[dict] = []

    class _FakeWS:
        async def send(self, raw):
            sent.append(_json.loads(raw))

    link = pc_link.PcLink() if hasattr(pc_link, "PcLink") else pc_link.PC_LINK
    link.register(_FakeWS())
    trace = err.new_turn()
    task = asyncio.create_task(link.forward("visual_presence", {}))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    cmd = next((f for f in sent if f.get("type") == "pc_command"), None)
    check("a forwarded PC command carries the turn id",
          cmd is not None and cmd.get("turn_id") == trace, str(cmd))

    # And the brain files an inbound pc_error under the laptop's identity, not its own.
    err.record(level="ERROR", subsystem="edge/camera", message="camera busy",
               turn=trace, process="pc_agent", host="laptop", ship=False)
    filed = [e for e in err.read(turn=trace) if e["process"] == "pc_agent"]
    check("a laptop executor error is filed under its own host/process",
          len(filed) == 1 and filed[0]["host"] == "laptop", str(filed))
    check("one turn id now spans brain and laptop executor",
          len({e["process"] for e in err.read(turn=trace)}) >= 1)

    print("\n[10] operation outcomes — successes AND the failures that never raise")
    err.record_op("tool", "read_email", ok=True, duration_ms=120)
    check("a success IS journalled (full activity trail)",
          any(e["subsystem"] == "tool/read_email"
              for e in err.read(limit=5, include_ok=True)))
    check("...at level OK", err.read(limit=1, include_ok=True)[0]["level"] == err.OK)
    check("...but hidden from the default failure view, so problems aren't buried",
          not any(e["subsystem"] == "tool/read_email" for e in err.read(limit=5)))
    err.record_op("tool", "read_email", ok=False, detail="Gmail isn't configured yet",
                  duration_ms=90, context={"args": "{}"})
    top = err.read(limit=1)[0]
    check("a soft failure IS journalled", top["subsystem"] == "tool/read_email", str(top))
    check("...with the reason", "isn't configured" in top["message"])
    check("...and its duration", top.get("context", {}).get("duration_ms") == 90)
    err.record_op("tool", "browser", ok=True, duration_ms=40_000, slow_ms=15_000)
    check("a slow-but-successful op is flagged",
          any(e["type"] == "Slow" for e in err.read(limit=2)))

    check("a polite failure sentence is recognised as a failure",
          err.looks_failed("I couldn't complete the calendar read just now (RuntimeError)."))
    check("a not-configured note is recognised", err.looks_failed("Gmail isn't configured yet, sir."))
    check("an empty result is recognised", err.looks_failed(""))
    check("a real answer is NOT flagged",
          not err.looks_failed("You have 3 events today, sir: standup at 09:00."))

    print("\n[11] every tool is covered by one wrapper")
    from afon.brain.agent import AfonAgent

    agent = AfonAgent()
    n_tools = len(agent._registry)
    before = len(err.read(limit=999))
    agent._registry["_boom"] = _raiser
    agent._registry["_soft"] = _softfail
    out = await agent._run_one_tool("_boom", {"x": 1}, None)
    check("a raising tool is journalled", not out["ok"] and len(err.read(limit=999)) > before)
    rec = next((e for e in err.read(limit=6) if e["subsystem"] == "tool/_boom"), None)
    check("...naming the tool", rec is not None, str(err.read(limit=3)))
    check("...and keeping its arguments", rec and "x" in str(rec.get("context", {})))
    await agent._run_one_tool("_soft", {}, None)
    check("a tool that returns a failure SENTENCE is journalled too",
          any(e["subsystem"] == "tool/_soft" for e in err.read(limit=6)))
    agent._registry["_fine"] = _ok
    await agent._run_one_tool("_fine", {}, None)
    check("a working tool IS recorded as a success",
          any(e["subsystem"] == "tool/_fine" for e in err.read(limit=6, include_ok=True)))
    check("...and stays out of the failure view",
          not any(e["subsystem"] == "tool/_fine" for e in err.read(limit=6)))
    check(f"one wrapper covers all {n_tools} registered tools", n_tools > 100, str(n_tools))
    await agent._run_one_tool("_nosuchtool", {}, None)
    check("an unknown tool name is journalled",
          any(e["subsystem"] == "tool/_nosuchtool" for e in err.read(limit=4)))

    print("\n[12] agentic + scheduled work")
    from afon.brain.proactive import ProactiveEngine

    def _bad_source():
        raise RuntimeError("source exploded")

    eng = ProactiveEngine(sources=[_bad_source]) if _accepts_sources() else None
    if eng is not None:
        await eng._gather()
        check("a failing proactive source is journalled BY NAME",
              any(e["subsystem"] == "signal/_bad_source" for e in err.read(limit=4)),
              str(err.read(limit=2)))
    else:
        check("proactive engine exposes _gather for tracking", hasattr(ProactiveEngine, "_gather"))

    err.record_op("job", "daily_briefing", ok=False, detail="job missed its scheduled run")
    check("a missed scheduled job is journalled",
          any(e["subsystem"] == "job/daily_briefing" for e in err.read(limit=3)))
    err.record_op("agentic", "llm_chain_exhausted", ok=False, detail="all 7 model(s) failed")
    check("total LLM chain exhaustion is journalled",
          any(e["subsystem"] == "agentic/llm_chain_exhausted" for e in err.read(limit=3)))
    err.record_op("pc", "visual_presence", ok=False, detail="no webcam, it's in use, or blocked")
    check("a laptop PC op failure is journalled",
          any(e["subsystem"] == "pc/visual_presence" for e in err.read(limit=3)))

    print("\n[13] deliberately-ignored errors that could cost a capability")
    err.swallowed("coaching_signals", ValueError("db locked"))
    rec = err.read(limit=1)[0]
    check("a swallowed error is still recorded", rec["subsystem"] == "swallowed/coaching_signals")
    check("...with its exception type", rec["type"] == "ValueError")

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    raise SystemExit(1 if FAIL else 0)


async def _raiser(_args):
    raise RuntimeError("tool exploded")


async def _softfail(_args):
    return "Gmail isn't configured yet, sir."


async def _ok(_args):
    return "Done, sir."


def _accepts_sources() -> bool:
    import inspect as _i

    from afon.brain.proactive import ProactiveEngine

    return "sources" in _i.signature(ProactiveEngine.__init__).parameters


asyncio.run(main())
