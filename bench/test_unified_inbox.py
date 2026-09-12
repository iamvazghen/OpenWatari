"""38.F2/38.F3 — one answer to "what's waiting", and nothing sent on a paraphrase.

Afon could read mail, read Telegram, and list the actions autonomous work had deferred. Three
surfaces, three turns, three shapes — and the owner held the join in his head, which is the same as
not having the answer, because the reason to ask is to find the thing you had forgotten.

What this asserts:

  * one sweep covers every channel, and a slow channel costs that channel, not the answer;
  * a thread is one thing to answer no matter how many messages it holds;
  * two channels are never merged just because they share a subject — that would hide one of them;
  * a channel that could not be read is NAMED, never counted as empty;
  * a send is confirmed on its actual words, not on a description the model wrote of them.

Hermetic: every channel is replaced. No Gmail, no Telegram, no network, no credentials.

    uv run python bench/test_unified_inbox.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
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


async def main() -> None:
    from afon.brain.tools import inbox as ib
    from afon.brain.proactive import CONFIRM_TIER, DRAFTED, confirm_required, draft_preview

    now = time.time()

    print("[1] a thread is ONE thing to answer, however many messages it holds")
    rows = [
        ib.Waiting("email", "t1", "Jane", "Invoice 88", 1, now - 300),
        ib.Waiting("email", "t1", "Jane", "Re: Invoice 88", 1, now - 60),
        ib.Waiting("email", "t1", "Jane", "Re: Invoice 88", 1, now - 30),
        ib.Waiting("email", "t2", "Bob", "Lunch?", 1, now - 900),
    ]
    out = ib.merge(rows)
    check("three messages of one thread collapse", len(out) == 2, [r.subject for r in out])
    one = next(r for r in out if r.thread == "t1")
    check("...carrying how many are in it", one.count == 3, one)
    check("...and showing the LATEST, not the first", one.subject == "Re: Invoice 88", one)
    check("the newest thread is first", out[0].thread == "t1", [r.thread for r in out])

    print("\n[2] two channels are never folded together")
    # The tempting bug: a subject-only key would merge these and silently drop one of the two
    # things he has to answer. Merging is the one operation that can LOSE information here.
    cross = ib.merge([
        ib.Waiting("email", "same", "Jane", "the party", 1, now - 100),
        ib.Waiting("telegram", "same", "Jane", "the party", 1, now - 50),
    ])
    check("a shared thread id in two channels stays two rows", len(cross) == 2, cross)
    check("...and both are named", {r.channel for r in cross} == {"email", "telegram"}, cross)

    print("\n[3] a channel with no thread id of its own still folds correctly")
    k = ib._thread_key
    check("'Re:' is not a different conversation",
          k({"who": "Jane", "subject": "Re: Invoice"}, "email")
          == k({"who": "jane", "subject": "Invoice"}, "email"))
    check("...nor is a German 'AW:'",
          k({"who": "Jane", "subject": "AW: Invoice"}, "email")
          == k({"who": "Jane", "subject": "Invoice"}, "email"))
    check("...nor 'Fwd:'",
          k({"who": "J", "subject": "Fwd: X"}, "email") == k({"who": "J", "subject": "X"}, "email"))
    check("two different subjects are two threads",
          k({"who": "J", "subject": "X"}, "email") != k({"who": "J", "subject": "Y"}, "email"))
    check("a real thread id always wins over the fallback",
          k({"thread": "abc", "who": "J", "subject": "X"}, "email") == "abc")

    print("\n[4] one hung channel costs that channel, not the answer  [budget: sweep ≤5s]")
    real = dict(ib.SOURCES)

    async def hang():
        await asyncio.sleep(30)
        return []

    async def quick_tg():
        return [ib.Waiting("telegram", "c9", "Bob", "you up?", 1, now)]

    async def quick_ap():
        return [ib.Waiting("approval", "a1", "me", "send_email(to=bank)", 1, now)]

    ib.SOURCES.update({"email": hang, "telegram": quick_tg, "approvals": quick_ap})
    started = time.monotonic()
    got, unknown = await ib.waiting(timeout=0.3)
    elapsed = time.monotonic() - started
    check("the sweep returns rather than waiting on the slow one", elapsed < 3.0, f"{elapsed:.2f}s")
    check("the channels that answered are all reported", len(got) == 2, got)
    check("the one that didn't is NAMED, not counted as empty", unknown == ["email"], unknown)

    print("\n[5] 'nothing waiting' and 'I couldn't look' are different sentences")
    check("with everything read and nothing there, he says so plainly",
          "Nothing's waiting on you" in ib.spoken([], []), ib.spoken([], []))
    quiet = ib.spoken([], ["email"])
    check("with a channel unread, he does NOT say nothing is waiting",
          "Nothing's waiting on you" not in quiet, quiet)
    check("...he names the channel he couldn't reach", "email" in quiet, quiet)
    check("...and warns the list is incomplete", "may not be all of it" in quiet, quiet)
    partial = ib.spoken(got, ["email"])
    check("a partial answer carries the same warning", "may not be all of it" in partial, partial)

    print("\n[6] the spoken answer is one paragraph a person can act on")
    said = ib.spoken(ib.merge(rows))
    check("it counts what is waiting", said.startswith("4 things waiting"), said)
    check("...across how many channels", "1 channel," in said, said)
    check("...names who each is from", "Jane" in said and "Bob" in said, said)
    check("...and marks a thread that stacked up", "(3)" in said, said)
    many = ib.merge([ib.Waiting("email", f"t{i}", f"P{i}", f"S{i}", 1, now - i) for i in range(12)])
    check("a long list is truncated, and says how much it kept back",
          "and 6 more" in ib.spoken(many), ib.spoken(many))

    print("\n[7] a source that raises is unknown, never empty")
    async def boom():
        raise RuntimeError("mailbox on fire")

    ib.SOURCES.update({"email": boom, "telegram": quick_tg, "approvals": quick_ap})
    got2, unknown2 = await ib.waiting(timeout=1.0)
    check("an exploding channel is reported as unreadable", unknown2 == ["email"], unknown2)
    check("...and the rest of the sweep still lands", len(got2) == 2, got2)
    ib.SOURCES.clear()
    ib.SOURCES.update(real)

    print("\n[8] the whole thing is one tool the model can reach")
    from afon.brain.tools import core_tool_schemas, tool_handlers

    names = {s["function"]["name"] for s in core_tool_schemas()}
    check("whats_waiting is advertised on every turn, not behind a keyword",
          "whats_waiting" in names)
    check("...and it has a handler", "whats_waiting" in tool_handlers())
    check("it takes no arguments — there is one question it answers",
          not (next(s for s in core_tool_schemas()
                    if s["function"]["name"] == "whats_waiting")["function"]["parameters"]
               .get("properties")))

    print("\n[9] 38.F3 — nothing goes out without the owner seeing the words")
    for tool in ("send_email", "send_telegram", "send_push", "place_call"):
        check(f"{tool} is confirm-gated", tool in CONFIRM_TIER)
    body = "Hi Jane, the invoice is attached. Best, Alex"
    draft = draft_preview("send_email", {"to": "jane@x.com", "subject": "Invoice", "body": body})
    check("the draft shows the recipient", "To: jane@x.com" in draft, draft)
    check("...the subject", "Subject: Invoice" in draft, draft)
    check("...and the body WORD FOR WORD, unclipped", body in draft, draft)
    long_body = "word " * 400
    check("a long message is not truncated — the tail is where the mistake hides",
          long_body.strip() in draft_preview("send_telegram", {"to": "me", "message": long_body}))
    check("an empty message is shown as empty, not hidden",
          "(no message text)" in draft_preview("send_telegram", {"to": "me", "message": ""}))
    check("a tool that isn't a send has no draft", draft_preview("ha_call", {"domain": "light"}) == "")
    check("every drafted tool is also confirm-gated — a preview is not a substitute for a yes",
          all(t in CONFIRM_TIER for t in DRAFTED), sorted(set(DRAFTED) - CONFIRM_TIER))
    check("a send with no recipient still confirms",
          confirm_required("send_email", {"body": "x"}))

    print("\n[10] the gate hands the model the draft, and tells it not to improve it")
    src = (Path(__file__).resolve().parents[1]
           / "src/afon/brain/agent.py").read_text(encoding="utf-8")
    check("the confirm gate builds a draft preview", "draft_preview(name, args)" in src)
    check("...and demands it be read back verbatim", "WORD FOR WORD" in src)
    check("...forbidding a summary", "not summarise or improve it" in src)

    print("\n[11] a DEFERRED send is approved on its words too")
    from afon.brain.approvals import ApprovalQueue

    q = ApprovalQueue(Path(tempfile.mkdtemp()) / "approvals.json")
    q.enqueue("send_email", {"to": "bank@x.com", "subject": "Late payment",
                             "body": "Please find the transfer reference below. " + "detail " * 30},
              origin="objective:pay-the-invoice")
    listing = q.render()
    check("the queued action names its recipient", "bank@x.com" in listing, listing)
    check("...and carries the full body, not a three-arg summary",
          "detail detail" in listing and listing.count("detail") > 20, listing[:200])
    q.enqueue("delete_task", {"id": "7"})
    check("a non-send queues with no draft block — nothing to read out",
          q.render().count("To: ") == 1, q.render())

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
