"""Phase 5.3 — the ambient HUD snapshot, hermetic.

Locks the projection: objectives / working-on / awaiting-approval / presence read correctly from
injected sources, the empty state is clean, and a source that RAISES yields an empty section instead
of sinking the whole snapshot (a status page must never 500 the brain).

    uv run python bench/test_hud.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.hud import hud_snapshot  # noqa: E402

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class FakeObj:
    def __init__(self, text, project="", latest=None, deferred=(), updated="2026-07-22"):
        self.text = text
        self.project = project
        self.progress = [{"ts": "2026-07-22", "note": latest}] if latest else []
        self.deferred = list(deferred)
        self.updated = updated


class FakeBook:
    def __init__(self, items):
        self._items = items

    def active(self):
        return self._items


class FakeTask:
    def __init__(self, title, elapsed="2m 3s", note="", kind="fleet"):
        self.title = title
        self._elapsed = elapsed
        self.last_progress = note
        self.kind = kind

    def human_elapsed(self):
        return self._elapsed


class FakeTasks:
    def __init__(self, items):
        self._items = items

    def active(self):
        return self._items


class FakeApproval:
    def __init__(self, summary, origin=""):
        self.summary = summary
        self.origin = origin


class FakeApprovals:
    def __init__(self, items):
        self._items = items

    def pending(self):
        return self._items


class FakePresence:
    def __init__(self, line):
        self._line = line

    def current_line(self):
        return self._line


class Boom:
    """A source whose every access raises — proves per-section fail-quiet."""

    def active(self):
        raise RuntimeError("source down")

    def pending(self):
        raise RuntimeError("source down")

    def current_line(self):
        raise RuntimeError("source down")


def main() -> None:
    print("[1] populated snapshot reads every section")
    d = hud_snapshot(
        objectives=FakeBook([
            FakeObj("Get Party Map beta launch-ready", project="party-map",
                    latest="wrote the smoke test", deferred=["publish the landing page"]),
            FakeObj("Grow Rently to 3 cities", latest="drafted Köln copy"),
        ]),
        tasks=FakeTasks([FakeTask("research competitor pricing", elapsed="4m", note="reading 3 sites")]),
        approvals=FakeApprovals([FakeApproval("send_email(to=bob@x.com)", origin="objective:party-map")]),
        presence=FakePresence("Right now you're in VS Code, sir."),
    )
    check("has a generated timestamp", isinstance(d.get("generated"), str) and "T" in d["generated"])
    check("presence carried through", d["presence"].startswith("Right now"))
    check("two objectives", len(d["objectives"]) == 2)
    check("objective keeps its project", d["objectives"][0]["project"] == "party-map")
    check("objective latest note", d["objectives"][0]["latest"] == "wrote the smoke test")
    check("objective awaiting list", d["objectives"][0]["awaiting"] == ["publish the landing page"])
    check("un-started objective reads cleanly", d["objectives"][1]["latest"] == "drafted Köln copy")
    check("working-on has the task + elapsed", d["working_on"][0]["title"] == "research competitor pricing"
          and d["working_on"][0]["elapsed"] == "4m")
    check("awaiting-approval summary + origin", d["awaiting_approval"][0]["summary"].startswith("send_email")
          and d["awaiting_approval"][0]["origin"] == "objective:party-map")
    check("systems block present", d["systems"]["brain"] == "online" and "llm" in d["systems"])
    check("fallbacks is an int count", isinstance(d["systems"]["fallbacks"], int))

    print("\n[2] empty snapshot is clean, not crashy")
    e = hud_snapshot(objectives=FakeBook([]), tasks=FakeTasks([]),
                     approvals=FakeApprovals([]), presence=FakePresence(""))
    check("no objectives -> []", e["objectives"] == [])
    check("no tasks -> []", e["working_on"] == [])
    check("no approvals -> []", e["awaiting_approval"] == [])
    check("systems still reported when idle", e["systems"]["brain"] == "online")

    print("\n[3] a broken source yields an empty section, never a crash")
    b = hud_snapshot(objectives=Boom(), tasks=FakeTasks([]), approvals=Boom(), presence=Boom())
    check("broken objectives -> []", b["objectives"] == [])
    check("broken approvals -> []", b["awaiting_approval"] == [])
    check("broken presence -> ''", b["presence"] == "")
    check("healthy section still fills alongside a broken one", b["working_on"] == []
          and b["systems"]["brain"] == "online")

    print("\n[4] caps: never floods the page")
    many = hud_snapshot(
        objectives=FakeBook([FakeObj(f"objective {i}") for i in range(20)]),
        tasks=FakeTasks([FakeTask(f"task {i}") for i in range(20)]),
        approvals=FakeApprovals([FakeApproval(f"do_{i}()") for i in range(20)]),
        presence=FakePresence("idle"),
    )
    check("objectives capped at 8", len(many["objectives"]) == 8)
    check("working-on capped at 8", len(many["working_on"]) == 8)
    check("approvals capped at 8", len(many["awaiting_approval"]) == 8)

    print("\n[5] live observability sections (performance / health / reliability)")
    check("performance block present", isinstance(d.get("performance"), dict))
    check("performance carries latency percentiles",
          "llm_p50_ms" in d["performance"] and "llm_p95_ms" in d["performance"], str(d.get("performance")))
    check("performance carries turn/tool counters",
          "turns" in d["performance"] and "tool_errors" in d["performance"])
    check("health is a list (empty ok when no probe yet)", isinstance(d.get("health"), list))
    check("reliability is a list (empty when all healthy)", isinstance(d.get("reliability"), list))
    # These read process singletons; a failure inside any must still leave a fail-quiet empty section.
    check("observability sections never crash the snapshot",
          "performance" in b and "health" in b and "reliability" in b)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
