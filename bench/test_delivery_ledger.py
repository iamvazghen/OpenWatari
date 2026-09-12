"""13.F3 — delivered is not seen, and an unacknowledged urgent item is re-raised once.

Every sender in this repo returns a boolean and forgets, so "he never answered" was unanswerable:
an urgent push into a coat pocket and an urgent push he acted on within a minute left exactly the
same trace. This asserts the three states are distinguishable, that the one allowed inference is
the only one taken, and — the part that matters most — that the second chance is a second chance
and not a loop.

Hermetic: the ledger is redirected into a temp dir, a fake engine stands in for the proactive
scheduler, and no notification is ever sent.

    uv run python bench/test_delivery_ledger.py
"""

from __future__ import annotations

import asyncio
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


def main() -> None:
    from afon.brain import delivery as d

    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    d._path = lambda: Path(tmp.name) / "ledger.json"  # type: ignore[assignment]
    T = 1_000_000.0
    LATER = T + d.RERAISE_AFTER_S

    print("[1] the three states are actually distinguishable")
    d.record("visa", "your visa is due Friday", urgency=0.9, channel="push", now=T)
    check("a sent item reads delivered", d.state_of("visa") == d.DELIVERED, str(d.state_of("visa")))
    check("delivered is NOT seen", d.state_of("visa") != d.SEEN)
    d.record("build", "the build is red", urgency=0.95, channel="voice", now=T)
    d.mark("build", d.SEEN, "replied in-session (neutral)", now=T + 30)
    check("seen is recorded when there is evidence", d.state_of("build") == d.SEEN)
    d.mark("build", d.ACTED, "accepted the interjection", now=T + 90)
    check("acted-on is its own state", d.state_of("build") == d.ACTED)
    check("an item never sent has no state", d.state_of("ghost") is None)

    print("\n[2] a state change with no evidence is refused, and states never go backwards")
    check("no evidence, no state change", not d.mark("visa", d.SEEN, ""))
    check("...and it is still delivered", d.state_of("visa") == d.DELIVERED)
    check("a late transport ack cannot un-see something",
          not d.mark("build", d.DELIVERED, "ntfy retry ack", now=T + 200))
    check("...the earlier evidence still stands", d.state_of("build") == d.ACTED)
    check("an unsent key cannot be marked", not d.mark("ghost", d.SEEN, "wishful thinking"))

    print("\n[3] the re-raise is ONE second chance — the whole point of the task")
    check("a fresh urgent item is not due yet", d.due_for_reraise(now=T + 60) == [])
    due = [r["key"] for r in d.due_for_reraise(now=LATER)]
    check("an unacknowledged urgent item comes due", due == ["visa"], str(due))
    check("an ACKNOWLEDGED urgent item never comes due", "build" not in due, str(due))
    check("the re-raise can be spent once", d.mark_reraised("visa", now=LATER))
    check("...and only once", not d.mark_reraised("visa", now=LATER + 1))
    check("...and it never comes back", d.due_for_reraise(now=LATER * 3) == [])

    print("\n[4] a non-urgent miss is a miss, not a nag")
    d.record("stretch", "stand up", urgency=0.5, channel="push", now=T)
    keys = [r["key"] for r in d.due_for_reraise(now=LATER * 3)]
    check("below the urgent bar, nothing is re-raised", keys == [], str(keys))
    check("the urgent bar is stated, not scattered", 0.5 < d.URGENT <= 1.0, str(d.URGENT))

    print("\n[5] the re-raise signal exists, is urgent, and cannot re-enter the ledger")
    d.record("passport", "your passport expires in a week", urgency=0.9, channel="push", now=T)
    # due_for_reraise reads the real clock here, so age the row instead of freezing time.
    rows = d._load()
    for r in rows:
        if r["key"] == "passport":
            r["at"] = 0.0
    d._save(rows)
    sigs = asyncio.run(d.reraise_signals())
    check("one signal per unacknowledged item", len(sigs) == 1, str([s.key for s in sigs]))
    check("it quotes what he missed", sigs and "passport expires" in sigs[0].message,
          sigs[0].message if sigs else "")
    check("it keeps the original's urgency", sigs and sigs[0].urgency == 0.9)
    check("its key is marked as a re-raise", sigs and sigs[0].key.startswith(d.RERAISE_PREFIX))

    # Generating a signal is not delivering it. Quiet hours, the daily budget and focus modes all
    # hold signals AFTER a source produces them, so spending the second chance here would burn it
    # on a message he never heard — the exact failure this task exists to close.
    check("merely GENERATING the re-raise does not spend it",
          len(asyncio.run(d.reraise_signals())) == 1)

    from afon.brain.proactive import ProactiveEngine

    eng = ProactiveEngine.__new__(ProactiveEngine)
    eng._record_delivery(sigs[0], "push")
    check("delivering it IS what spends it", asyncio.run(d.reraise_signals()) == [])
    # The loop this must not become: if the re-raise were itself recorded, it is urgent by
    # construction and would come due for its own re-raise half an hour later — forever.
    check("a re-raise is NEVER recorded as a fresh delivery",
          d.state_of(sigs[0].key) is None, str(d.state_of(sigs[0].key)))

    print("\n[6] the engine records what it sent, with the channel it sent it on")
    from afon.brain.proactive import Signal

    eng._record_delivery(Signal(key="k-new", kind="health", urgency=0.85, message="vault is gone"),
                         "voice")
    row = d._find(d._load(), "k-new")
    check("the delivery is on the ledger", row is not None)
    check("with its channel", row and row["channel"] == "voice", str(row))
    check("and it starts at delivered, never higher", row and row["state"] == d.DELIVERED)

    print("\n[7] the source is registered, so the second chance actually ticks")
    from afon.brain.proactive import default_signal_sources

    names = {getattr(s, "__name__", "") for s in default_signal_sources()}
    check("reraise_signals is a tick source", "reraise_signals" in names, str(sorted(names)))

    print("\n[8] the server reads the KEY before grading resolves the pending slot")
    src = (Path(__file__).resolve().parents[1] / "src" / "afon" / "brain" / "server.py").read_text(
        encoding="utf-8")
    i_key = src.find("pending_key(now)")
    i_fb = src.find("record_feedback(kind")
    check("pending_key is read before record_feedback clears it", 0 < i_key < i_fb,
          f"key at {i_key}, feedback at {i_fb}")
    check("a dismissal still counts as seen", "SEEN" in src and 'verdict == "positive"' in src)

    tmp.cleanup()
    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
