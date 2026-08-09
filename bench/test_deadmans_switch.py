"""Phase 0.3 — dead-man's switch: on a sustained brain outage, attempt ONE remote restart + alert.

Extends the (already-tested) edge-triggered WatchState with the reaction layer: the down-edge runs
the restart command exactly once and folds the result into the single alert; a still-down tick never
re-fires; recovery announces once. Hermetic — alert + restart are injected, no network, no shell.

    uv run python bench/test_deadmans_switch.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "deploy"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from uptime_watch import WatchState, handle_edge  # noqa: E402

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
    alerts: list[str] = []
    restarts: list[str] = []

    def alert(text: str):
        alerts.append(text)
        return True

    def restart_ok(cmd: str):
        restarts.append(cmd)
        return True, "afon-brain restarted"

    def restart_fail(cmd: str):
        restarts.append(cmd)
        return False, "ssh: connect timed out"

    print("[1] down-edge with a restart command: restart runs once, alert reports success")
    alerts.clear(); restarts.clear()
    msg = handle_edge("down", "http://vps/healthz", 2, restart_cmd="ssh vps restart",
                      alert_fn=alert, restart_fn=restart_ok)
    check("restart command was run once", restarts == ["ssh vps restart"], repr(restarts))
    check("exactly one alert fired", len(alerts) == 1)
    check("alert says the restart ran", "✓ ran" in alerts[0], repr(alerts[0]))
    check("alert carries the outage marker", "DOWN" in msg)

    print("\n[2] down-edge, restart FAILS: still one alert, marked as failed")
    alerts.clear(); restarts.clear()
    handle_edge("down", "http://vps/healthz", 3, restart_cmd="ssh vps restart",
                alert_fn=alert, restart_fn=restart_fail)
    check("restart attempted", len(restarts) == 1)
    check("alert marks the restart as failed", "✗ failed" in alerts[0], repr(alerts[0]))

    print("\n[3] no restart command configured: alert only, nothing run")
    alerts.clear(); restarts.clear()
    handle_edge("down", "http://vps/healthz", 2, restart_cmd=None, alert_fn=alert, restart_fn=restart_ok)
    check("alerted", len(alerts) == 1)
    check("no restart attempted when unconfigured", restarts == [])

    print("\n[4] a no-edge tick does nothing; recovery announces once")
    alerts.clear(); restarts.clear()
    check("None edge -> no alert, no restart",
          handle_edge(None, "u", 0, restart_cmd="x", alert_fn=alert, restart_fn=restart_ok) is None
          and alerts == [] and restarts == [])
    handle_edge("up", "u", 0, restart_cmd="x", alert_fn=alert, restart_fn=restart_ok)
    check("recovery alert fired, no restart on recovery", len(alerts) == 1 and restarts == [] and "UP" in alerts[0])

    print("\n[5] end-to-end over an outage: exactly one restart across many still-down ticks")
    alerts.clear(); restarts.clear()
    st = WatchState(fails_needed=2)
    for ok in [True, False, False, False, False, True]:  # up, then a 4-tick outage, then recovery
        edge = st.observe(ok)
        handle_edge(edge, "http://vps/healthz", st.consecutive_fails,
                    restart_cmd="ssh vps restart", alert_fn=alert, restart_fn=restart_ok)
    check("restart fired exactly once for the whole outage (no storm)", len(restarts) == 1, repr(restarts))
    check("one down alert + one up alert", len(alerts) == 2, repr(alerts))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
