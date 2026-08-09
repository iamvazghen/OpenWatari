"""Off-VPS uptime watcher — alerts when the 24/7 brain goes dark (TODO #1 P2, single-VPS SPOF).

A watcher that lives ON the VPS can't warn you the VPS is down — it's down too. So this runs
somewhere ELSE (the laptop, a Pi, any always-on box) and pings the brain's ``/healthz`` on a fixed
interval. After ``--fails`` consecutive misses it fires ONE Telegram alert; when the brain comes
back it fires ONE recovery notice. It never spams — exactly one message per state transition.

Config comes from the same ``.env`` the brain uses (so a fork configures it once):
  * ``AFON_BRAIN_HOST`` / ``AFON_CLIENT_HTTP_PORT``  → the healthz URL (or pass ``--url``)
  * ``AFON_TELEGRAM_BRIDGE_BOT_TOKEN`` (or ``AFON_TELEGRAM_BOT_TOKEN``) + ``AFON_TELEGRAM_DEFAULT_CHAT``

Run it under a laptop scheduler / a tiny systemd service on a second box:
    uv run python deploy/uptime_watch.py --url http://<vps>:8766/healthz --interval 120 --fails 2

The alerting STATE MACHINE (edge-triggered, no repeats) is pure and unit-tested in
``bench/test_uptime_watch.py`` — the network I/O is a thin shell around it.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field


@dataclass
class WatchState:
    """Edge-triggered outage detector: fire once on the down-crossing, once on recovery.

    ``fails_needed`` consecutive failures flips ``down`` True and returns "down" ONCE. The next
    success flips it back and returns "up" ONCE. Every other tick returns None (silence).
    """

    fails_needed: int = 2
    consecutive_fails: int = 0
    down: bool = False
    # kept for observability / tests
    history: list[bool] = field(default_factory=list)

    def observe(self, ok: bool) -> str | None:
        """Feed one probe result. Returns 'down', 'up', or None (no state change to announce)."""
        self.history.append(ok)
        if ok:
            self.consecutive_fails = 0
            if self.down:
                self.down = False
                return "up"          # recovery edge
            return None
        # a failure
        self.consecutive_fails += 1
        if not self.down and self.consecutive_fails >= self.fails_needed:
            self.down = True
            return "down"            # outage edge
        return None


def _probe(url: str, timeout: float) -> bool:
    import httpx

    try:
        r = httpx.get(url, timeout=timeout)
        return r.status_code == 200 and "ok" in r.text.lower()
    except Exception:  # noqa: BLE001 — any error is a failed probe
        return False


def _alert(text: str) -> bool:
    """Send a Telegram message via the bridge/bot token. Returns True on success."""
    import httpx

    from afon.config import settings

    token = settings.telegram_bridge_bot_token or settings.telegram_bot_token
    chat = settings.telegram_default_chat
    if not token or not chat:
        print(f"[uptime] (no telegram configured) would alert: {text}")
        return False
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat, "text": text},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:  # noqa: BLE001
        print(f"[uptime] telegram send failed: {type(e).__name__}: {e}")
        return False


def _restart(cmd: str, timeout: float = 90.0) -> tuple[bool, str]:
    """Run the remote-restart command (usually an ``ssh … systemctl --user restart afon-brain``).

    Returns (ok, short_output). Any failure is captured, never raised — a dead-man's switch that
    crashes is no switch at all.
    """
    import subprocess

    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        return p.returncode == 0, out[:400]
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def handle_edge(edge: str | None, url: str, fails: int, *, restart_cmd: str | None = None,
                alert_fn=_alert, restart_fn=_restart) -> str | None:
    """React to a WatchState edge: on 'down', optionally attempt a remote restart, then alert ONCE;
    on 'up', announce recovery. Returns the alert text (or None on a no-edge tick).

    This is the dead-man's switch: because ``WatchState`` is edge-triggered, the restart is attempted
    exactly once per outage — no restart storm while the brain is still coming back. Side-effects are
    injected (``alert_fn`` / ``restart_fn``) so the whole reaction is unit-testable with no network.
    """
    if edge == "down":
        msg = (f"🔴 Afon brain is DOWN — {url} failed {fails}x. The 24/7 assistant is unreachable.")
        if restart_cmd:
            ok, out = restart_fn(restart_cmd)
            msg += f"\nRemote restart attempted: {'✓ ran' if ok else '✗ failed'}."
            if out:
                msg += f" ({out[:120]})"
        alert_fn(msg)
        return msg
    if edge == "up":
        msg = "🟢 Afon brain is back UP — healthz responding again."
        alert_fn(msg)
        return msg
    return None


def _default_url() -> str:
    from afon.config import settings

    host = settings.brain_host if settings.brain_host not in ("0.0.0.0", "") else "127.0.0.1"
    return f"http://{host}:{settings.client_http_port}/healthz"


def main() -> None:
    ap = argparse.ArgumentParser(description="Off-VPS uptime watcher for the Afon brain")
    ap.add_argument("--url", default=None, help="healthz URL (default from .env brain host/port)")
    ap.add_argument("--interval", type=float, default=120, help="seconds between probes")
    ap.add_argument("--fails", type=int, default=2, help="consecutive misses before alerting")
    ap.add_argument("--timeout", type=float, default=8, help="per-probe HTTP timeout (s)")
    ap.add_argument("--once", action="store_true", help="probe once and exit (for cron)")
    ap.add_argument("--restart-cmd", default=None,
                    help="shell command run ONCE when the brain goes down (dead-man's switch), e.g. "
                         "\"ssh openclaw@<vps> 'systemctl --user restart afon-brain'\"")
    args = ap.parse_args()

    url = args.url or _default_url()
    state = WatchState(fails_needed=args.fails)
    print(f"[uptime] watching {url} every {args.interval}s, alert after {args.fails} misses"
          + (f", auto-restart on outage" if args.restart_cmd else ""))

    def tick() -> None:
        ok = _probe(url, args.timeout)
        edge = state.observe(ok)
        if edge:
            handle_edge(edge, url, state.consecutive_fails, restart_cmd=args.restart_cmd)
            print(f"[uptime] {'ALERT: brain down' if edge == 'down' else 'recovery: brain back up'}")
        else:
            print(f"[uptime] {'ok' if ok else 'MISS'} (fails={state.consecutive_fails}, down={state.down})")

    if args.once:
        tick()
        return
    while True:
        tick()
        time.sleep(max(10.0, args.interval))


if __name__ == "__main__":
    main()
