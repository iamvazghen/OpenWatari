"""Phase 4.2 — the approval queue: deferred outward steps become approvable actions, hermetic.

Locks the queue (enqueue/dedup/find/approve-executes/reject/prune/persistence), the worker wiring (a
confirm-gated step is QUEUED with its real tool+args, not just noted), the owner tools, and the safety
rule that the autonomous worker can never approve its own deferrals.

    uv run python bench/test_approvals.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.approvals import ApprovalQueue, describe  # noqa: E402
import afon.brain.tools.approvals as atools  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def tmp_queue() -> ApprovalQueue:
    return ApprovalQueue(Path(tempfile.mkdtemp()) / "approvals.json")


async def main() -> None:
    print("[1] queue: enqueue / dedup / pending order / persistence")
    q = tmp_queue()
    a1 = q.enqueue("send_email", {"to": "bob@x.com", "subject": "Hi"}, origin="objective:launch")
    check("enqueue returns an approval", a1 is not None and a1.status == "pending")
    check("summary reads like a call", a1.summary.startswith("send_email(to=bob@x.com"), a1.summary)
    same = q.enqueue("send_email", {"to": "bob@x.com", "subject": "Hi"}, origin="objective:launch")
    check("identical pending step dedups", same.id == a1.id and len(q.pending()) == 1)
    q.enqueue("publish_page", {"id": "party"}, origin="objective:launch")
    check("distinct step queues separately", len(q.pending()) == 2)
    check("empty tool rejected", q.enqueue("", {}) is None)
    reloaded = ApprovalQueue(q._path)
    check("persists + reloads", len(reloaded.pending()) == 2)

    print("\n[2] find: ordinal / id / word / ambiguous")
    check("'first' -> oldest pending", q.find("first").id == a1.id)
    check("bare/empty -> oldest pending", q.find("").id == a1.id)
    check("by id", q.find(a1.id).id == a1.id)
    check("by word", q.find("publish").tool == "publish_page")
    check("no match -> None", q.find("nonsense-zzz") is None)

    print("\n[3] approve EXECUTES the real tool with its real args")
    seen = {}

    async def fake_send(args):
        seen.update(args)
        return "Email sent."

    ok, out = await q.approve(a1.id, registry={"send_email": fake_send})
    check("approve reports success", ok and out == "Email sent.")
    check("executed with the original args", seen == {"to": "bob@x.com", "subject": "Hi"})
    check("approved item leaves the queue", len(q.pending()) == 1)
    check("status recorded", q._items[a1.id].status == "approved")
    ok2, _ = await q.approve(a1.id, registry={"send_email": fake_send})
    check("cannot approve twice", ok2 is False)

    print("\n[4] approve failure paths")
    q2 = tmp_queue()
    b1 = q2.enqueue("vanished_tool", {"x": 1})
    ok3, msg3 = await q2.approve(b1.id, registry={})
    check("missing handler -> failed, not raised", ok3 is False and q2._items[b1.id].status == "failed")

    async def boom(args):
        raise RuntimeError("nope")

    b2 = q2.enqueue("explodes", {"y": 2})
    ok4, msg4 = await q2.approve(b2.id, registry={"explodes": boom})
    check("tool exception -> failed, not raised", ok4 is False and q2._items[b2.id].status == "failed")
    check("failure message is calm", "failed" in msg4.lower(), msg4)

    print("\n[5] reject + prune")
    q3 = tmp_queue()
    c1 = q3.enqueue("delete_everything", {"path": "/"})
    check("reject removes from pending", q3.reject(c1.id, "absolutely not") and len(q3.pending()) == 0)
    check("reject is recorded", q3._items[c1.id].status == "rejected")
    check("cannot reject twice", q3.reject(c1.id) is False)
    for i in range(60):
        x = q3.enqueue("t", {"i": i})
        q3.reject(x.id)
    dropped = q3.prune(keep=50)
    check("prune trims old resolved items", dropped > 0 and len([a for a in q3._items.values() if a.status != "pending"]) <= 50)

    print("\n[6] worker wiring: a deferred step is QUEUED with real tool+args")
    from afon.brain.worker import TaskWorker

    wq = tmp_queue()

    class Call:
        def __init__(self, name, args_json):
            self.id = "c1"
            self.function = type("F", (), {"name": name, "arguments": args_json})()

    class FakeLLM:
        def __init__(self):
            self.n = 0

        async def complete(self, messages, tools=None, tool_choice=None, skip_primary=False):
            self.n += 1
            if self.n == 1:
                return type("M", (), {"content": "", "tool_calls": [Call("send_email", '{"to":"a@b.c"}')]})()
            return type("M", (), {"content": "Drafted it; the send needs you.", "tool_calls": None})()

    w = TaskWorker(FakeLLM(), {"send_email": lambda a: None}, [], max_steps=3,
                   origin="objective:test", approvals=wq)
    res = await w.run("email the landlord")
    check("worker still returns a written result", "Drafted it" in res, res)
    check("worker text still lists the approval", "Needs your approval" in res, res)
    pend = wq.pending()
    check("deferred step was QUEUED", len(pend) == 1 and pend[0].tool == "send_email", str(pend))
    check("queued with real args", pend[0].args == {"to": "a@b.c"}, str(pend[0].args if pend else None))
    check("queued with its origin", pend[0].origin == "objective:test")

    print("\n[7] SAFETY: the worker can never approve its own deferrals")
    from afon.brain.agent import AfonAgent

    names = {s["function"]["name"] for s in AfonAgent._worker_tools(object.__new__(AfonAgent))}
    check("approve_action withheld from worker", "approve_action" not in names)
    check("reject_action withheld from worker", "reject_action" not in names)
    check("but a normal tool is present", "web_search" in names or len(names) > 20, str(len(names)))

    print("\n[8] owner-facing tools")
    tq = tmp_queue()
    atools.APPROVALS = tq
    check("nothing pending -> calm line", "Nothing's waiting" in await atools.list_approvals({}))
    tq.enqueue("send_email", {"to": "z@y.x"}, origin="objective:launch")
    r = await atools.list_approvals({})
    check("lists what's waiting", "send_email" in r and "waiting on you" in r, r)
    called = {}

    async def ok_tool(args):
        called["hit"] = True
        return "Sent."

    import afon.brain.tools as toolsmod
    orig = toolsmod.tool_handlers
    toolsmod.tool_handlers = lambda: {"send_email": ok_tool}
    try:
        r = await atools.approve_action({"topic": "first"})
    finally:
        toolsmod.tool_handlers = orig
    check("approve tool executes it", called.get("hit") is True and "Done, sir" in r, r)
    tq.enqueue("publish_page", {"id": "p"})
    r = await atools.reject_action({"topic": "publish"})
    check("reject tool drops it", "Dropped it" in r and len(tq.pending()) == 0, r)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
