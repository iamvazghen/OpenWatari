"""No background loop runs unseen — SYSTEMS.md 31.F4, the standing "no silent work" clause.

The clause says every background loop declares its **period**, its **budget** and its **kill
switch**, and appears in the HUD. Nothing enforced it, and the bill came twice: a focus sprint that
was dead for three days while every surface stayed green, and seven crons reporting ``status=ok``
with a failed tool inside them. Neither was a hard failure. Both were loops nobody could see.

Four properties, and only the second one is hard to keep:

  1. the declarations are complete — a period, a budget, a kill switch, a real module;
  2. **every loop-spawning site in the brain is classified** — declared as a loop, or named as
     one-shot work with a reason. This is the check that bites when someone adds a new loop: an
     unclassified ``create_task`` fails here, and that is the entire point of the file;
  3. a tick records what happened and never swallows an exception on the way;
  4. the HUD serves it, including the loops that have never run.

Scope is the **brain process**, deliberately. The HUD is a projection of brain state and the edge is
a separate process on another host with its own supervisor and audio watchdog; claiming to cover it
from here would be a gate that reads green about something it cannot see.

Run:
    uv run python bench/test_loop_registry.py
"""

from __future__ import annotations

import ast
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from afon.brain import loops as L  # noqa: E402

BRAIN = ROOT / "src" / "afon" / "brain"

#: site key -> the declared loop it starts. A site is `file::spawn-target`, keyed on the target
#: rather than the enclosing function because four different loops start inside `server.serve`.
LOOP_SITES: dict[str, str] = {
    "maintenance.py::run_maintenance": "memory-maintenance",
    "presence.py::self.run_poller": "presence-poller",
    "proactive.py::self.run": "proactive-tick",
    "scheduler.py::_fire_briefing": "daily-task-briefing",
    "scheduler.py::_fire_objectives": "daily-objectives",
    "scheduler.py::_fire_backlog": "daily-backlog",
    "scheduler.py::_fire_backup": "daily-memory-backup",
    "scheduler.py::_fire_pattern_scan": "daily-pattern-scan",
    "scheduler.py::_fire_restore_drill": "daily-restore-drill",
    "scheduler.py::_fire_weekly_review": "weekly-memory-review",
    "server.py::_fire_reliability_probe": "reliability-health-probe",
    "server.py::_fire_composio_catalog_refresh": "composio-catalog-refresh",
    "server.py::tg_bridge.run": "telegram-bridge",
}

#: site key -> why it is NOT a background loop. Every one of these is work that starts, finishes and
#: is gone; none of them recurs on its own. A one-shot has nothing to be stale about.
ONE_SHOT: dict[str, str] = {
    "agent.py::coro": "one tool call raced against a progress ping",
    "agent.py::_run": "per-turn background work — dies with the turn",
    "llm.py::_push": "one page to the owner when the model chain fails over",
    "protocols.py::_deliver_report": "one report, when a protocol run ends",
    "scheduler.py::_fire": "a reminder the OWNER set — created per request, fires once",
    "server.py::self._broadcast_assistant": "one outbound message to connected clients",
    "server.py::self._run_turn": "one turn",
    "server.py::_refresh_now": "one catalogue refresh at start-up; the recurring one is declared",
    "server.py::daily_digest.build_body": "one digest built for one session's first turn",
    "server.py::self._send": "one message on the wire",
    "tasks.py::_runner": "one queued background task — the queue recurs, the task does not",
}

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def _target(node: ast.Call) -> str:
    """What this call actually schedules — the first positional argument, unwrapped."""
    if not node.args:
        return "<none>"
    a = node.args[0]
    if isinstance(a, ast.Await):
        a = a.value
    if isinstance(a, ast.Call):
        a = a.func
    if isinstance(a, ast.Name):
        return a.id
    if isinstance(a, ast.Attribute):
        return ast.unparse(a)
    return ast.unparse(a)[:40]


def scan(root: Path) -> dict[str, list[str]]:
    """site key -> the lines it appears on. Finds every way this codebase starts detached work."""
    sites: dict[str, list[str]] = {}
    for p in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = p.relative_to(root).as_posix()
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if nm in ("create_task", "ensure_future", "add_job"):
                sites.setdefault(f"{rel}::{_target(n)}", []).append(f"{rel}:{n.lineno}")
    return sites


def main() -> int:
    print("[1] every declared loop declares the three things the clause requires")
    names = [lp.name for lp in L.DECLARED]
    check(f"declarations are uniquely named ({len(names)} loops)", len(set(names)) == len(names))
    bad_budget = [lp.name for lp in L.DECLARED if not lp.budget_ms or lp.budget_ms <= 0]
    check("every loop declares a budget", not bad_budget, str(bad_budget))
    bad_kill = [lp.name for lp in L.DECLARED if not (lp.kill or "").strip()]
    check("every loop declares a kill switch", not bad_kill, str(bad_kill))
    bad_sched = [lp.name for lp in L.DECLARED if not (lp.schedule or "").strip()]
    check("every loop declares a schedule", not bad_sched, str(bad_sched))
    # A cron job's period is fixed and must be stated. A poller reads its interval from settings, so
    # its number only exists at run time — set_period records it when the loop starts.
    no_period = [lp.name for lp in L.DECLARED if lp.kind == "cron" and not lp.period_s]
    check("every cron loop states its period", not no_period, str(no_period))
    bad_kind = [lp.name for lp in L.DECLARED if lp.kind not in ("cron", "poller", "listener")]
    check("every loop has a known kind", not bad_kind, str(bad_kind))
    missing_mod = [lp.name for lp in L.DECLARED
                   if not (BRAIN / (lp.module.split(".")[-1] + ".py")).is_file()]
    check("every loop names a module that exists", not missing_mod, str(missing_mod))

    print("\n[2] every way the brain starts detached work is classified")
    sites = scan(BRAIN)
    known = set(LOOP_SITES) | set(ONE_SHOT)
    unclassified = sorted(set(sites) - known)
    check(f"no unclassified spawn site ({len(sites)} sites: "
          f"{len(LOOP_SITES)} loops, {len(ONE_SHOT)} one-shots)",
          not unclassified,
          "\n      new detached work — declare it in afon.brain.loops or name it in ONE_SHOT:\n      "
          + "\n      ".join(f"{k}  ({', '.join(sites[k])})" for k in unclassified))
    # The tables must not rot in the other direction either: a key for a site that no longer exists
    # is a classification nobody is checking, and it hides the next real one behind noise.
    phantom = sorted(known - set(sites))
    check("no classification for a site that no longer exists", not phantom, str(phantom))
    undeclared = sorted({v for v in LOOP_SITES.values()} - set(L.BY_NAME))
    check("every classified loop site names a DECLARED loop", not undeclared, str(undeclared))
    unstarted = sorted(set(L.BY_NAME) - set(LOOP_SITES.values()))
    check("every declared loop has a real spawn site", not unstarted,
          f"declared but nothing starts it: {unstarted}")

    print("\n[3] the scan actually catches a new loop (planted)")
    # The check above passes today by construction. Prove it fails when it should: parse a module
    # that starts something new, and confirm the site surfaces as unclassified.
    tmp = BRAIN / "_tmp_loop_probe.py"
    tmp.write_text("import asyncio\n"
                   "async def _silent(): ...\n"
                   "def go(): asyncio.create_task(_silent())\n", encoding="utf-8")
    try:
        planted = scan(BRAIN)
        key = "_tmp_loop_probe.py::_silent"
        check("a newly spawned loop shows up as unclassified",
              key in planted and key not in known,
              "the scan missed a create_task — every future silent loop would pass too")
    finally:
        tmp.unlink(missing_ok=True)

    print("\n[4] a tick records what happened, and never swallows it")
    L.reset()
    before = L.snapshot()
    check("a fresh process reports every loop as never run",
          all(r["never_ran"] and r["runs"] == 0 for r in before))
    check("and says nothing is wrong — a restart is not an incident", L.problems() == [])
    with L.tick("proactive-tick"):
        pass
    row = {r["name"]: r for r in L.snapshot()}["proactive-tick"]
    check("one tick is recorded with a duration",
          row["runs"] == 1 and not row["never_ran"] and row["last_ms"] is not None,
          str(row))
    raised = False
    try:
        with L.tick("proactive-tick"):
            raise ValueError("boom")
    except ValueError:
        raised = True
    row = {r["name"]: r for r in L.snapshot()}["proactive-tick"]
    check("an exception inside a tick is re-raised, not swallowed", raised,
          "a registry that eats the loop's errors is worse than no registry")
    check("and it is counted, with the reason kept",
          row["errors"] == 1 and "ValueError" in row["last_error"], str(row["last_error"]))
    check("the failed tick still counts as a tick — it ran, it just failed",
          row["runs"] == 2, "otherwise a loop that fails every time reads as a loop that stopped")

    print("\n[5] the numbers mean what the HUD will say they mean")
    L.reset()
    now = time.time()
    L._rec("daily-pattern-scan").last_tick = now - 86_400        # one period: on schedule
    L._rec("memory-maintenance").last_tick = now - 3 * 86_400    # three: it stopped
    L._rec("telegram-bridge").last_tick = now - 30 * 86_400      # a quiet month is not a fault
    rows = {r["name"]: r for r in L.snapshot(now)}
    check("a loop that ticked one period ago is not stale", not rows["daily-pattern-scan"]["stale"])
    check("a loop silent for three periods is stale", rows["memory-maintenance"]["stale"])
    # A listener declares no period, and that IS the mechanism — nothing else exempts it. An earlier
    # version also tested `kind != "listener"` here, which no plant could ever falsify because a
    # listener has no period to be late against.
    check("a listener has no period, so a quiet month is not a fault",
          rows["telegram-bridge"]["period_s"] is None and not rows["telegram-bridge"]["stale"])
    probs = L.problems(now)
    check("and the stale one is the only thing reported",
          len(probs) == 1 and probs[0].startswith("memory-maintenance"), str(probs))
    L.reset()
    with L.tick("presence-poller"):
        time.sleep((L.BY_NAME["presence-poller"].budget_ms + 20) / 1000.0)
    row = {r["name"]: r for r in L.snapshot()}["presence-poller"]
    check("a run over its declared budget is counted as an overrun", row["overruns"] == 1,
          f"{row['last_ms']}ms against a {row['budget_ms']}ms budget")
    L.reset()

    print("\n[6] each declared loop is really ticked, in the module it claims")
    # A declaration nobody records is worse than no declaration: the row sits at never_ran forever,
    # so the surface permanently reports a dead loop and the owner learns to ignore the section.
    unticked = []
    for lp in L.DECLARED:
        src = (BRAIN / (lp.module.split(".")[-1] + ".py")).read_text(encoding="utf-8")
        if f'"{lp.name}"' not in src and f"'{lp.name}'" not in src:
            unticked.append(f"{lp.name} (not recorded in {lp.module})")
    check("every declared loop names itself in a tick call", not unticked, str(unticked))

    print("\n[7] the HUD serves it — which is what 31.F4 actually asks for")
    from afon.brain.hud import hud_snapshot

    snap = hud_snapshot(objectives=_Empty(), tasks=_Empty(), approvals=_Empty(),
                        presence=_Empty())
    hud_loops = snap.get("loops") or []
    check(f"the HUD carries a loops section with every declared loop ({len(hud_loops)})",
          {r["name"] for r in hud_loops} == set(L.BY_NAME))
    need = {"last_tick", "period_s", "budget_ms", "kill"}
    check("each row carries its last tick, its period, its budget and its kill switch",
          all(need <= set(r) for r in hud_loops),
          "31.F4 names those four by name")
    check("the loops section is fail-quiet like every other HUD section",
          _hud_survives_a_broken_section(),
          "a status page that 500s during an incident is a status page for the good days")

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    return 0 if FAIL == 0 else 1


class _Empty:
    """Stand-in for the HUD's singletons — this file is about loops, not objectives."""

    def active(self):
        return []

    def pending(self):
        return []

    def current_line(self):
        return ""


def _hud_survives_a_broken_section() -> bool:
    import afon.brain.hud as hud

    original = hud._loops
    hud._loops = lambda: (_ for _ in ()).throw(RuntimeError("registry exploded"))
    try:
        snap = hud.hud_snapshot(objectives=_Empty(), tasks=_Empty(), approvals=_Empty(),
                                presence=_Empty())
        return snap.get("loops") == [] and bool(snap.get("assistant"))
    except Exception:  # noqa: BLE001
        return False
    finally:
        hud._loops = original


if __name__ == "__main__":
    raise SystemExit(main())
