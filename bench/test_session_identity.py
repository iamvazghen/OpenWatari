"""A conversation has a name, and nothing falls out of it silently (07.F1, 07.F2).

Two defects sat behind S07's bar — "a conversation survives a walk to the kitchen, a reconnect and
a night's sleep".

  * **No conversation identity.** Session ids on the wire were per-entry-point constants, so one
    laptop appeared as `laptop-1` or `laptop-edge` depending on which launcher ran, and nothing
    tied a session to the conversation it belonged to.
  * **The trim discarded.** `_trim` kept the last N turns and dropped the rest on the floor. Only
    the *reset* path ever journalled, so a conversation long enough to trim lost its opening
    silently — the model could no longer see it and nothing had written it down.

The checks below are mostly about the seams, because both defects were invisible in normal use and
would come back the same way: a new entry point with its own id constant, or a trim that stops
handing its leftovers anywhere.

Hermetic: a temp state dir for the id, a stubbed journal for the trim. No network, no model.

    uv run python bench/test_session_identity.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.shared import session as SESS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

#: Entry points that open a link to the brain. Each must derive its id, not carry one.
EDGE_CLIENTS = ("src/afon/edge/remote_brain.py", "src/afon/edge/edge_lite.py")

#: The constants that used to be hardcoded. Their return would be the regression.
LEGACY_IDS = ("laptop-1", "laptop-edge", "android-1")

passed = failed = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {label}")
    else:
        failed += 1
        print(f"  [FAIL] {label}  {detail}")


def main() -> None:
    # Recorded before anything runs and asserted at the end. This is the check that would have
    # caught the original mistake, so it is a check and not a comment.
    real_session = Path.home() / ".afon" / "afon_session.json"
    real_before = real_session.stat().st_mtime if real_session.exists() else None

    print("[1] one conversation identity, and it survives")
    with tempfile.TemporaryDirectory() as d:
        saved = SESS._path
        SESS._path = lambda: Path(d) / "conversation_id"
        try:
            a = SESS.current()
            check(bool(SESS._ID_RE.match(a)), "a conversation id is created on first use", a)
            check(SESS.current() == a, "reading it again does not start a new conversation")

            # A reconnect is a new process reading the same file. That is the walk to the kitchen.
            check(SESS.device_session_id("laptop") == SESS.device_session_id("laptop"),
                  "two connects from one device produce the same session id")
            check(SESS.conversation_of(SESS.device_session_id("iphone")) == a,
                  "a second device joins the SAME conversation")
            check(SESS.device_session_id("LAPTOP") == SESS.device_session_id("laptop"),
                  "device names are normalised, so case cannot fork a session")

            sid = SESS.device_session_id("laptop")
            check(SESS.device_of(sid) == "laptop", "the device half is recoverable", sid)
            check(SESS.conversation_of(sid) == a, "the conversation half is recoverable", sid)

            print("\n[2] rotation is explicit, and only at a boundary")
            b = SESS.rotate("test")
            check(b != a, "rotate produces a different conversation")
            check(SESS.current() == b, "and it persists, so a restart stays in the new one")

            print("\n[3] ids that carry no conversation are not given one")
            for legacy in LEGACY_IDS:
                check(SESS.conversation_of(legacy) is None,
                      f"`{legacy}` carries no conversation, and none is invented")
            check(SESS.device_of("laptop-1") == "laptop-1",
                  "a legacy id still names its device, so its turns are attributable")
            check(SESS.conversation_of("laptop:zzz") is None,
                  "a malformed conversation half is rejected, not passed through")

            # A corrupt file must start a fresh conversation rather than propagate nonsense.
            SESS._path().write_text("not-an-id", encoding="utf-8")
            c = SESS.current()
            check(bool(SESS._ID_RE.match(c)) and c != b,
                  "a corrupt id file starts a new conversation instead of failing", c)
        finally:
            SESS._path = saved

    print("\n[4] no entry point carries a session id of its own")
    for rel in EDGE_CLIENTS:
        body = (ROOT / rel).read_text(encoding="utf-8")
        check("device_session_id(" in body, f"{rel} derives its session id")
        for legacy in LEGACY_IDS:
            check(f'"{legacy}"' not in body, f"{rel} no longer hardcodes `{legacy}`")

    agent_src = (ROOT / "src/afon/brain/agent.py").read_text(encoding="utf-8")
    reset = agent_src.split("def reset_session(", 1)
    check(len(reset) == 2 and "rotate" in reset[1][:1500],
          "the conversation id turns over inside reset_session, where the boundary already is")

    print("\n[5] the trim is lossy on purpose, and hands over what it drops  [trim retains gist]")
    trim = agent_src.split("def _trim(", 1)[1][:900]
    check("_remember_dropped" in trim, "the trim hands its leftovers somewhere")
    check("self._history[:-max_msgs]" in trim,
          "the dropped slice is captured before the window is reassigned")

    from afon.config import settings  # noqa: E402

    # HERMETIC OR NOT AT ALL, AND BEFORE THE CONSTRUCTOR RUNS. `AfonAgent.__init__` calls
    # `_load_session()`, which reads the restart-durable snapshot from the real state directory and
    # UNLINKS it when it is older than the idle-reset window. Patching the path on the instance is
    # too late — construction has already touched the owner's real working memory, which is exactly
    # what the first version of this file did. `session_persist_path` is redirected here, before the
    # agent is imported, so nothing below can reach the real file at all.
    tmpdir = tempfile.mkdtemp()
    _saved_persist = settings.session_persist_path
    settings.session_persist_path = str(Path(tmpdir) / "session.json")

    from afon.brain.agent import AfonAgent  # noqa: E402

    def _hermetic(a, tmp: Path):
        a._session_path = lambda: tmp          # type: ignore[assignment]
        a._history = []
        a._dropped = []
        return a

    agent = _hermetic(AfonAgent(max_history_turns=2), Path(tmpdir) / "s1.json")
    journalled: list[list[dict]] = []

    async def _fake_journal(history):
        journalled.append(list(history))
        return "summary"

    agent._journal_history = _fake_journal   # type: ignore[assignment]
    agent._dropped_flush_turns = 2

    async def drive() -> None:
        for i in range(10):
            agent._history.append({"role": "user", "content": f"u{i}"})
            agent._history.append({"role": "assistant", "content": f"a{i}"})
            agent._trim()
        # let the background flush task run
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.run(drive())

    check(len(agent._history) <= 4, "the window is still bounded", str(len(agent._history)))
    check(bool(journalled), "trimmed messages reached the journal instead of the floor")
    seen = {m["content"] for batch in journalled for m in batch} | {
        m["content"] for m in agent._history} | {m["content"] for m in agent._dropped}
    missing = [f"u{i}" for i in range(10) if f"u{i}" not in seen]
    check(not missing, "every user turn is either in the window, journalled, or still buffered",
          f"lost: {missing}")

    # Nothing may be summarised twice — a duplicated journal entry is a wrong memory, not a
    # harmless one, because the reviewer distils facts from it. Counted over THIS test's synthetic
    # turns only: identical content can legitimately repeat in a real conversation (the same
    # refusal to two different prompts), so equality of text is not evidence of double-journalling.
    mine = [m["content"] for batch in journalled for m in batch
            if m.get("content", "") in {f"u{i}" for i in range(10)} | {f"a{i}" for i in range(10)}]
    check(len(mine) == len(set(mine)), "no message is journalled twice", str(mine))
    check(len(mine) == len([m for b in journalled for m in b]),
          "and nothing but this test's turns reached the journal — the agent is hermetic",
          str([m["content"] for b in journalled for m in b])[:200])

    print("\n[6] a reset flushes what the trim has not summarised yet")
    agent2 = _hermetic(AfonAgent(max_history_turns=2), Path(tmpdir) / "s2.json")
    got: list[list[dict]] = []

    async def _fake_journal2(history):
        got.append(list(history))
        return "s"

    agent2._journal_history = _fake_journal2  # type: ignore[assignment]
    agent2._dropped = [{"role": "user", "content": "old-and-unsummarised"}]
    agent2._history = [{"role": "user", "content": "recent"},
                       {"role": "assistant", "content": "reply"}]

    async def do_reset() -> None:
        agent2.reset_session(reason="test")
        for _ in range(5):
            await asyncio.sleep(0)

    asyncio.run(do_reset())
    flushed = [m["content"] for batch in got for m in batch]
    check("old-and-unsummarised" in flushed,
          "the pending buffer is journalled at reset, not dropped with it", str(flushed))
    check(flushed.index("old-and-unsummarised") < flushed.index("recent"),
          "and it is ordered before what is still in the window")
    check(agent2._dropped == [], "the buffer is cleared once it has been handed over")

    print("\n[7] this test left the real state directory alone")
    settings.session_persist_path = _saved_persist
    real_after = real_session.stat().st_mtime if real_session.exists() else None
    check(real_after == real_before,
          "the owner's session snapshot was neither written nor deleted",
          f"before={real_before} after={real_after}")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
