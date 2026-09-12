"""Daily catch-up digest — one delivery per channel per day, no duplication.

Proves the fix for the "7-8 duplicate Telegram messages a day" bug:
  * build_body composes past-due tasks (Notion + local queue) + important email into one line;
  * due()/mark_delivered() gate each channel ('push','edge') to once per local day, persisted;
  * the brain appends the digest to the owner's FIRST live-edge turn ("By the way, sir — …"),
    alongside the normal answer — and NOT on later turns the same day (no repeats);
  * task/email nudges are no longer per-tick proactive sources (so they can't spam the day).

Hermetic: temp state file, mocked digest sources, a fake agent + websocket. No network.

    uv run python bench/test_daily_digest.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
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


class FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)


class FakeAgent:
    """Streams a fixed two-sentence answer; records proactive notes."""

    def __init__(self) -> None:
        self.notes: list[str] = []

    async def warmup(self) -> None:  # noqa: D401
        pass

    async def respond_stream(self, text, on_progress=None):
        for s in ["It's sunny in Berlin, sir.", "About 20 degrees."]:
            yield s

    def note_proactive(self, m: str) -> None:
        self.notes.append(m)


def _assistant_text(ws: FakeWS) -> str:
    """Concatenate the assistant deltas the brain sent to a client."""
    out = []
    for raw in ws.sent:
        d = json.loads(raw)
        if d.get("kind") == "assistant":
            out.append(d.get("delta", ""))
    return "".join(out)


async def main() -> None:
    import afon.brain.daily_digest as dd
    from afon.brain.server import BrainServer
    from afon.shared.protocol import Utterance

    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    state_file = Path(tmp.name) / "digest_state.json"
    dd._state_path = lambda: state_file  # type: ignore[assignment]

    print("[1] build_body composes past-due tasks + important email into one line")
    import afon.brain.tools.notion as nt
    import afon.brain.tools.gmail as gm

    async def fake_overdue():
        return (["rent (2d overdue)", "call the bank (1d overdue)"], ["submit report"])

    async def fake_email():
        return "3 important emails unread, the latest from Jane"

    nt.overdue_and_today = fake_overdue
    gm.important_email_phrase = fake_email
    body = await dd.build_body()
    check("body names past-due tasks", "2 past-due tasks" in body and "rent (2d overdue)" in body, body)
    check("body names due-today", "1 due today: submit report" in body, body)
    check("body names important email", "3 important emails unread" in body, body)

    print("\n[2] empty sources -> empty body")
    nt.overdue_and_today = lambda: _empty2()
    gm.important_email_phrase = lambda: _empty1()
    check("no tasks + no email -> ''", (await dd.build_body()) == "")

    print("\n[2b] a failed source degrades its section, never the brief  [source down]")

    async def boom_overdue():
        raise RuntimeError("notion 404")

    async def boom_email():
        raise RuntimeError("gmail token expired")

    # One source down, the other fine: the good section is still delivered, and the gap is named.
    nt.overdue_and_today = boom_overdue
    gm.important_email_phrase = fake_email
    body = await dd.build_body()
    check("a working section still reaches him", "3 important emails unread" in body, body)
    check("the failed source is NAMED", "task board" in body, body)
    check("and the gap is admitted", "may be incomplete" in body, body)
    check("last_failed() reports the id", dd.last_failed() == ["notion"], str(dd.last_failed()))

    # Every source down. '' would be read by the caller as "nothing worth saying", which is how the
    # single most alarming morning produced silence.
    nt.overdue_and_today = boom_overdue
    gm.important_email_phrase = boom_email
    body = await dd.build_body()
    check("total failure is NOT silence", body != "", repr(body))
    check("it says a full brief is not possible", "can't give you a full brief" in body, body)
    check("and names both sources", "task board" in body and "your mail" in body, body)
    check("last_failed() reports both", dd.last_failed() == ["notion", "gmail"],
          str(dd.last_failed()))

    # The contract that a genuinely empty day stays empty must survive all of the above.
    nt.overdue_and_today = lambda: _empty2()
    gm.important_email_phrase = lambda: _empty1()
    check("a complete brief with nothing in it is still ''", (await dd.build_body()) == "")
    check("and last_failed() is clear again", dd.last_failed() == [], str(dd.last_failed()))

    print("\n[3] due()/mark_delivered() gate each channel once per day, persisted")
    dd._save({})
    check("edge due on a fresh day", dd.due("edge"))
    check("push due on a fresh day", dd.due("push"))
    dd.mark_delivered("edge")
    check("edge no longer due after mark", not dd.due("edge"))
    check("push still due (independent channel)", dd.due("push"))
    # persisted across a 'restart' (re-read the file)
    check("state persisted to disk", json.loads(state_file.read_text("utf-8")).get("edge"))

    print("\n[4] first live-edge turn appends the catch-up alongside the answer")
    dd._save({})  # fresh day: edge due
    nt.overdue_and_today = fake_overdue
    gm.important_email_phrase = fake_email
    server = BrainServer(agent=FakeAgent())
    ws = FakeWS()
    await server._run_turn(ws, Utterance(type="utterance", session_id="s1", text="what's the weather?", ts_user_stop_ms=0))
    txt = _assistant_text(ws)
    check("answer still delivered", "sunny in Berlin" in txt, txt)
    check("catch-up appended ('By the way')", "By the way, sir" in txt, txt)
    check("catch-up carries the tasks", "rent (2d overdue)" in txt, txt)
    check("catch-up carries the email", "important emails unread" in txt, txt)
    check("edge marked delivered after the turn", not dd.due("edge"))
    check("addendum recorded to proactive history", any("By the way" in n for n in server._agent.notes))

    print("\n[5] a SECOND turn the same day does NOT repeat the catch-up (the anti-dup fix)")
    ws2 = FakeWS()
    await server._run_turn(ws2, Utterance(type="utterance", session_id="s1", text="and tomorrow?", ts_user_stop_ms=0))
    txt2 = _assistant_text(ws2)
    check("answer delivered on 2nd turn", "sunny in Berlin" in txt2)
    check("no catch-up on 2nd turn", "By the way" not in txt2, txt2)

    print("\n[6] nothing-due first turn: marks delivered, appends nothing")
    dd._save({})
    nt.overdue_and_today = lambda: _empty2()
    gm.important_email_phrase = lambda: _empty1()
    ws3 = FakeWS()
    await server._run_turn(ws3, Utterance(type="utterance", session_id="s1", text="hello", ts_user_stop_ms=0))
    txt3 = _assistant_text(ws3)
    check("no catch-up when nothing is due", "By the way" not in txt3)
    check("edge still marked delivered (won't rebuild all day)", not dd.due("edge"))

    print("\n[7] task/email nudges are NOT proactive tick sources anymore")
    from afon.brain.proactive import default_signal_sources
    names = {getattr(s, "__name__", "") for s in default_signal_sources()}
    check("task_signals not a tick source", "task_signals" not in names, str(names))
    check("email_signals not a tick source", "email_signals" not in names, str(names))

    try:
        tmp.cleanup()
    except Exception:  # noqa: BLE001
        pass

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


async def _empty2():
    return ([], [])


async def _empty1():
    return ""


if __name__ == "__main__":
    asyncio.run(main())
