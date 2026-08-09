"""Companion safety + memory hardening tests (offline, deterministic).

Covers four fixes from the 2026-06-16 audit:
  1. Confirmation tier is ENFORCED in code (not just the system prompt): an outward-facing /
     destructive tool is blocked until Vazghen affirms — a weak model can't fire it unprompted.
  2. Proactive messages persist durably (L2 journal), so a nudge is referable DAYS later, not just
     inside the 12-turn working window.
  3. Smart session reset clears stale WORKING memory after an idle gap (durable memory kept).
  4. write_vault saves notes only on the authoritative host, scoped safely inside the vault.

Run: uv run python bench/test_companion_safety.py
"""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from afon.brain.agent import USER_TZ, AfonAgent, _is_affirmation
from afon.config import settings

PASS = 0
FAIL = 0


def check(name: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  XX  {name}")


def test_affirmation() -> None:
    check("affirmation: 'yes, send it'", _is_affirmation("yes, send it"))
    check("affirmation: 'go ahead'", _is_affirmation("go ahead"))
    check("affirmation: 'do it'", _is_affirmation("do it"))
    check("non-affirmation: 'actually cancel that'", not _is_affirmation("actually cancel that"))
    check("non-affirmation: 'what time is it'", not _is_affirmation("what time is it"))


def test_begin_turn_supersede() -> None:
    agent = AfonAgent()
    agent._pending_confirm = {"name": "send_email", "args": {}}
    agent._last_turn_at = datetime.now(USER_TZ)
    agent._begin_turn("yes please")
    check("affirmation grants the pending confirm",
          agent._confirm_granted is True and bool(agent._pending_confirm))
    agent._pending_confirm = {"name": "send_email", "args": {}}
    agent._begin_turn("actually, what's the weather")
    check("a new non-affirmative request supersedes the pending one",
          agent._confirm_granted is False and agent._pending_confirm is None)


def test_smart_reset() -> None:
    agent = AfonAgent()
    agent._idle_reset_min = 180
    agent._history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    agent._last_turn_at = datetime.now(USER_TZ) - timedelta(minutes=200)
    agent._begin_turn("are you there")
    check("idle gap (>threshold) clears working memory", agent._history == [])

    agent._history = [{"role": "user", "content": "hi"}]
    agent._last_turn_at = datetime.now(USER_TZ) - timedelta(minutes=5)
    agent._begin_turn("still here?")
    check("recent gap keeps working memory", agent._history != [])

    agent._history = [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}]
    agent.reset_session("manual")
    check("explicit reset_session clears history", agent._history == [])


def test_proactive_durable() -> None:
    agent = AfonAgent()
    captured: list[str] = []
    from afon.brain import memory as memmod

    orig = memmod.STORE.journal_append
    memmod.STORE.journal_append = lambda summary, when=None: captured.append(summary)  # type: ignore
    try:
        agent.note_proactive("You have a standup in 10 minutes, sir.")
    finally:
        memmod.STORE.journal_append = orig  # type: ignore
    check("proactive note in working history (1-min recall)",
          any("standup" in m.get("content", "") for m in agent._history))
    check("proactive note persisted to journal (10-day recall)",
          any("[proactive]" in c and "standup" in c for c in captured))


async def _none() -> None:
    """`_face_second_factor` returns a refusal string or None; None = 'no reason to refuse'."""
    return None


async def test_confirm_gate() -> None:
    agent = AfonAgent()
    sent: list[dict] = []

    async def spy(args: dict) -> str:
        sent.append(args)
        return "sent"

    agent._registry["send_email"] = spy

    # Stub the FACE second factor. It fires only for confirm-gated actions and takes a real camera
    # burst, so this "hermetic" test was quietly dependent on whether a laptop webcam could see the
    # owner at that moment: it passed on one full-suite run and failed the next with
    # "REFUSED send_email — camera saw 0 face(s)", 14s apart, with no code change between them.
    # An intermittent test is worse than a missing one, because a real regression here reads as
    # "the camera again". What this function is testing is the GATE, not the camera; the camera
    # path has its own coverage in test_camera.py.
    agent._face_second_factor = lambda name, args: _none()
    calls = [{"id": "1", "name": "send_email", "arguments": '{"to":"anna","body":"late"}'}]

    agent._confirm_granted = False
    msgs: list[dict] = []
    await agent._execute_calls(msgs, calls)
    blocked = any("CONFIRM_REQUIRED" in m.get("content", "") for m in msgs if m.get("role") == "tool")
    check("confirm gate BLOCKS send_email without a yes",
          blocked and not sent and bool(agent._pending_confirm))

    agent._confirm_granted = True
    await agent._execute_calls([], calls)
    check("confirm gate RUNS send_email after a grant", len(sent) == 1)
    check("grant consumed (one yes = one action)",
          agent._confirm_granted is False and agent._pending_confirm is None)

    msgs3: list[dict] = []
    await agent._execute_calls(msgs3, [{"id": "2", "name": "get_time", "arguments": "{}"}])
    check("read tool (get_time) is never gated",
          not any("CONFIRM_REQUIRED" in m.get("content", "") for m in msgs3 if m.get("role") == "tool"))


async def test_vault_write() -> None:
    from afon.brain.tools.vault import write_vault

    settings.vault_writable = False
    r = await write_vault({"content": "hello"})
    check("vault write refused when host isn't authoritative", "off" in r.lower())

    old_path, old_w = settings.vault_path, settings.vault_writable
    with tempfile.TemporaryDirectory() as d:
        settings.vault_path = d
        settings.vault_writable = True
        try:
            await write_vault({"note": "Decisions", "content": "Chose MIT license.", "mode": "create"})
            f = Path(d) / "Afon" / "Decisions.md"
            check("vault write creates a note",
                  f.is_file() and "MIT" in f.read_text(encoding="utf-8"))
            await write_vault({"note": "Decisions", "content": "Added OpenAfon rename."})
            check("vault write appends to an existing note", "OpenAfon" in f.read_text(encoding="utf-8"))
            await write_vault({"note": "../../escape", "content": "x"})
            check("vault write can't traverse outside the vault",
                  not (Path(d).parent / "escape.md").exists())
        finally:
            settings.vault_path, settings.vault_writable = old_path, old_w


def test_ack_for() -> None:
    from afon.brain.agent import _ack_for

    check("ack is contextual (uses the query)", "BTC" in _ack_for("web_search", {"query": "BTC price"}))
    check("ack for delegate mentions the team lead", "team lead" in _ack_for("delegate_to_fleet", {"task": "x"}))
    check("ack ends politely ('…sir.')", _ack_for("get_time", {}).strip().endswith("sir."))
    check("unknown tool still gets an acknowledgement", "On it" in _ack_for("mystery_tool", {}))


def test_immediate_ack() -> None:
    # G1: the immediate ack now ROTATES a phrase pool (no fixed line), and stays silent when a
    # specific per-tool ack is imminent (a zero-arg read). So assert POOL membership, not one phrase.
    from afon.brain.agent import _WORK_ACKS, _CHAT_ACKS

    agent = AfonAgent()
    notes: list[str] = []
    agent._immediate_ack("set a reminder for 5pm", notes.append)   # arg-bearing command -> WORK pool
    check("immediate ack fires on a command", len(notes) == 1 and notes[0] in _WORK_ACKS)
    chatter: list[str] = []
    agent._immediate_ack("haha that's pretty funny", chatter.append)   # plain chatter -> CHAT pool
    check("plain accepted speech gets a brief acknowledgement",
          len(chatter) == 1 and chatter[0] in _CHAT_ACKS)


async def test_progress_watchdog() -> None:
    agent = AfonAgent()
    old_warn, old_every = settings.tool_slow_warn_seconds, settings.tool_long_update_seconds
    settings.tool_slow_warn_seconds = 0.08
    settings.tool_long_update_seconds = 0.08
    try:
        async def slow(_args: dict) -> str:
            await asyncio.sleep(0.3)
            return "done"

        agent._registry["slowtool"] = slow
        notes: list[str] = []
        msgs: list[dict] = []
        await agent._execute_calls(msgs, [{"id": "1", "name": "slowtool", "arguments": "{}"}],
                                   on_progress=notes.append)
        check("acknowledgement spoken before the tool", any("On it" in n for n in notes))
        check("watchdog speaks 'still on it' for a slow tool",
              any("still on it" in n.lower() for n in notes))
        check("slow tool still returns its result",
              any(m.get("role") == "tool" and "done" in m.get("content", "") for m in msgs))

        async def boom(_args: dict) -> str:
            raise RuntimeError("kaboom")

        agent._registry["boomtool"] = boom
        msgs2: list[dict] = []
        await agent._execute_calls(msgs2, [{"id": "2", "name": "boomtool", "arguments": "{}"}])
        check("watchdog re-raises -> recoverable error result",
              any("hit an error" in m.get("content", "") for m in msgs2 if m.get("role") == "tool"))
    finally:
        settings.tool_slow_warn_seconds, settings.tool_long_update_seconds = old_warn, old_every


def test_session_persistence() -> None:
    """Working thread survives a 'restart' (new agent) via the on-disk snapshot, and an expired
    snapshot is dropped on load."""
    old_path = settings.session_persist_path
    tmp = Path(tempfile.mkdtemp()) / "sess.json"
    settings.session_persist_path = str(tmp)
    try:
        a = AfonAgent()
        a._history = [{"role": "user", "content": "remember 42"},
                      {"role": "assistant", "content": "noted, sir"}]
        a._trim()  # persists
        check("snapshot file written on turn", tmp.exists())

        b = AfonAgent()  # simulates a brain restart
        check("new agent resumes the thread after restart",
              b._history == a._history and len(b._history) == 2)

        # An old snapshot (beyond the idle-reset window) must NOT be resurrected.
        import json as _j
        import time as _t
        tmp.write_text(_j.dumps({"saved_at": _t.time() - 4 * 3600,
                                 "history": [{"role": "user", "content": "stale"}]}), encoding="utf-8")
        c = AfonAgent()
        c._idle_reset_min = 180
        c._load_session()
        check("expired snapshot is dropped on load", c._history == [])

        # Manual reset clears the snapshot too.
        d = AfonAgent()
        d._history = [{"role": "user", "content": "x"}]
        d._trim()
        d.reset_session("manual")
        check("reset_session removes the snapshot file", not tmp.exists())
    finally:
        settings.session_persist_path = old_path


def test_wake_ack_config() -> None:
    """The wake-word acknowledgement phrase parses into random-choice options; empty disables."""
    choices = [p.strip() for p in (settings.wake_ack_phrase or "").split("|") if p.strip()]
    check("wake ack phrase yields >=1 spoken option", len(choices) >= 1)
    check("wake ack phrases are non-empty", all(choices))
    check("empty ack phrase disables (no options)",
          [p for p in "".split("|") if p.strip()] == [])


def main() -> None:
    test_affirmation()
    test_begin_turn_supersede()
    test_smart_reset()
    test_proactive_durable()
    test_ack_for()
    test_immediate_ack()
    test_session_persistence()
    test_wake_ack_config()
    asyncio.run(test_confirm_gate())
    asyncio.run(test_vault_write())
    asyncio.run(test_progress_watchdog())
    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    raise SystemExit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
