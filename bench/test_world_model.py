"""Phase 2.1 — the owner's world-model (durable goals/projects/deadlines + context).

Foundation for reasoned anticipation (2.3): the store the LLM reasons FORWARD from. Asserts upsert/
dedup, persistence across reload, deadline-ordered active goals, overdue/due-today rendering, status
transitions, and pruning of stale done goals. Hermetic — a temp file, an injected clock.

    uv run python bench/test_world_model.py
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.world_model import WorldModel  # noqa: E402

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
    now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "wm.json"

        print("[1] upsert + dedup on id, persists across reload")
        wm = WorldModel(path)
        wm.upsert_goal("rently-launch", "Ship the Rently 3-category launch", project="Rently",
                       deadline=(now + timedelta(days=4)).isoformat())
        wm.upsert_goal("rently-launch", "Ship the Rently launch + 2 niche kits", project="Rently",
                       deadline=(now + timedelta(days=4)).isoformat())  # update, not a 2nd goal
        wm.upsert_goal("party-map", "Party Map beta", project="PartyMap")  # no deadline
        wm2 = WorldModel(path)  # reload from disk
        check("dedup kept one Rently goal", len(wm2.active_goals(now)) == 2, str(len(wm2.active_goals(now))))
        check("the update won (latest text)", any("niche kits" in g.text for g in wm2.active_goals(now)))

        print("\n[2] active goals sorted soonest-deadline-first, deadline-less last")
        wm2.upsert_goal("passport", "Renew passport", deadline=(now + timedelta(days=1)).isoformat())
        order = [g.id for g in wm2.active_goals(now)]
        check("nearest deadline (passport, 1d) first", order[0] == "passport", repr(order))
        check("deadline-less goal (party-map) sorts last", order[-1] == "party-map", repr(order))

        print("\n[3] render surfaces overdue / due-today / due-in-Nd + context")
        wm2.upsert_goal("migration", "Push migrations 0007/0008", project="Rently",
                        deadline=(now - timedelta(days=2)).isoformat())  # overdue
        wm2.upsert_goal("standup", "Prep standup", deadline=now.isoformat())  # due today
        wm2.set_context("Flying to Yerevan Thursday; heads-down on Rently.")
        r = wm2.render(now=now, owner="Alex")
        check("overdue marked", "OVERDUE by 2d" in r, r)
        check("due-today marked", "due today" in r, r)
        check("due-in-days marked", "due in 4d" in r, r)
        check("context included", "Yerevan" in r, r)
        check("owner name in header", "Alex's current world" in r)

        print("\n[4] status transitions + prune old done goals")
        check("complete_goal flips status", wm2.complete_goal("standup") is True)
        check("completed goal drops out of active", "standup" not in [g.id for g in wm2.active_goals(now)])
        # Age a done goal 20 days into the past, then prune with a 14-day keep.
        wm2._goals["standup"].updated = (now - timedelta(days=20)).isoformat()
        wm2._save()
        pruned = WorldModel(path).prune(keep_done_days=14, now=now)
        check("stale done goal pruned", pruned == 1, str(pruned))
        check("a recent active goal is NOT pruned", "rently-launch" in [g.id for g in WorldModel(path).active_goals(now)])

        print("\n[5] empty model renders empty (nothing to say)")
        check("fresh model renders empty string", WorldModel(Path(td) / "empty.json").render(now=now) == "")

        print("\n[6] refresh_from_tasks: open tasks -> goals, done tasks -> completed (Phase 2.2)")
        wm3 = WorldModel(Path(td) / "wm3.json")
        up, done = wm3.refresh_from_tasks([
            {"id": "t1", "title": "File the Q3 taxes", "due": (now + timedelta(days=3)).isoformat(),
             "project": "Vardanian"},
            {"id": "t2", "title": "Call the vet", "project": "Personal"},
            {"id": "t3", "title": "Old finished thing", "done": True},  # complete_goal on a missing id = no-op
        ], now=now)
        check("two open tasks upserted as goals", up == 2, str(up))
        ids = [g.id for g in wm3.active_goals(now)]
        check("goal carries the deadline (taxes sorts before vet)", ids[0] == "t1", repr(ids))
        check("project mapped through", any(g.project == "Vardanian" for g in wm3.active_goals(now)))
        # A later refresh where t1 is now done -> it completes and drops from active.
        up2, done2 = wm3.refresh_from_tasks([{"id": "t1", "title": "File the Q3 taxes", "done": True}], now=now)
        check("completing a task removes it from active goals", "t1" not in [g.id for g in wm3.active_goals(now)])
        check("completed count reported", done2 == 1, str(done2))

        print("\n[7] note_event: integration events surface to the reasoner, then expire (Phase 2.4)")
        wm4 = WorldModel(Path(td) / "wm4.json")
        wm4.note_event("Stripe payout of €420 cleared", ttl_hours=24, now=now)
        wm4.note_event("CI failed on party-map main", ttl_hours=1, now=now)
        r = wm4.render(now=now, owner="Alex")
        check("recent events appear in the render", "Stripe payout" in r and "CI failed" in r, r)
        # events persist across reload
        check("events survive a reload", "Stripe payout" in WorldModel(Path(td) / "wm4.json").render(now=now))
        # 90 minutes later the 1h event has expired, the 24h one remains
        later = now + timedelta(minutes=90)
        r2 = wm4.render(now=later, owner="Alex")
        check("expired event drops out", "CI failed" not in r2, r2)
        check("unexpired event remains", "Stripe payout" in r2, r2)
        # an events-only model still renders (not empty)
        wm5 = WorldModel(Path(td) / "wm5.json")
        wm5.note_event("reply from the landlord arrived", now=now)
        check("events-only world still renders", "landlord" in wm5.render(now=now))

    states_its_gaps()

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


# ── 24.F2: say what is NOT known about an entity ─────────────────────────────────────────────
# Afon could always report what he knew about someone. He could not report what he didn't — so an
# entity he held two facts about and one he held twenty about sounded equally complete, and the
# owner had no way to tell which answers were thin. The gaps come from a DECLARED list of facets,
# not from whatever happens to be stored: a gap you can only notice by already knowing what to
# look for is a gap nobody notices. Same reasoning as the loop registry in 31.F4.
def states_its_gaps() -> None:
    import tempfile
    from pathlib import Path as _P

    from afon.brain.graph import GraphMemory
    from afon.brain.world_model import what_i_dont_know
    from afon.shared.entities import FACETS

    with tempfile.TemporaryDirectory() as td:
        g = GraphMemory(_P(td) / "graph.sqlite")
        g.add("Anna Petrova", "email", "anna@example.com")
        g.add("Anna Petrova", "role", "architect")

        print("\n[24.F2] the answer names both halves")
        said = what_i_dont_know("Anna Petrova", graph=g)
        check("what IS known is reported", "anna@example.com" in said, said)
        check("what is NOT known is reported too", "don't have" in said, said)
        for facet in ("phone", "birthday"):
            check(f"the missing '{facet}' is named", facet in said, said)
        check("a facet that IS known is not listed as missing",
              "role" not in said.split("don't have")[1], said)

        print("\n[24.F2] the gaps come from a declared list, not from what happens to be stored")
        gaps = g.gaps("Anna Petrova")
        check(f"every gap is a declared facet ({gaps})",
              set(gaps) <= set(FACETS["person"]), str(gaps))
        check("the two recorded facets are not gaps",
              "email" not in gaps and "role" not in gaps, str(gaps))
        check("a project is asked different questions than a person",
              set(g.gaps("Rently", kind="project")) <= set(FACETS["project"])
              and "deadline" in g.gaps("Rently", kind="project"),
              str(g.gaps("Rently", kind="project")))

        print("\n[24.F2] an entity he holds nothing about says so plainly")
        blank = what_i_dont_know("Someone He Never Met", graph=g)
        check("it does not pretend to know anything", "nothing recorded" in blank, blank)
        check("...and still says what it would want to know", "email" in blank, blank)

        print("\n[24.F2] the gaps close as facts arrive")
        before = len(g.gaps("Anna Petrova"))
        g.add("Anna Petrova", "phone", "+37411223344")
        after = g.gaps("Anna Petrova")
        check(f"recording a fact removes its gap ({before} -> {len(after)})",
              "phone" not in after and len(after) == before - 1, str(after))
        # 24.F1 and 24.F2 meet here: the gap must close whichever spelling the fact arrived under.
        g.add("Вазген", "email", "vaz@example.com")
        check("a fact stored under one spelling closes the gap for the other",
              "email" not in g.gaps("Vazgen"), str(g.gaps("Vazgen")))


if __name__ == "__main__":
    main()
