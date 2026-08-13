#!/usr/bin/env python3
"""Afon VPS ticker — the always-on host for RECURRING reminders (Phase 4b, true 24/7).

Why this exists
---------------
One-shot / `at` reminders already reach the phone with the PC off, because ntfy.sh holds them
server-side until their fire time (see ``brain/tools/notify.py``). But ntfy cannot hold a
*recurring* schedule ("every day at 08:00"). For that you need a process that is itself always
on. You run this on your own VPS (e.g. ``user@your-vps-host``); this tiny daemon runs there.

What it is
----------
A single self-contained file (no ``afon`` package import, so it deploys without the repo):
APScheduler drives the cron jobs; each fire POSTs to the SAME ntfy topic the edge uses, so the
reminder lands on the phone regardless of whether the laptop is on. A tiny HTTP endpoint lets the
edge register/list/cancel recurring reminders, so ``set_reminder(daily=…)`` on the PC stays the
single way Vazghen creates them — the ticker is just where the *recurring* ones also live.

It is deliberately decoupled from the OpenClaw fleet/gateway: it only knows ntfy + a JSON file.

Config (env)
------------
  AFON_NTFY_SERVER   default https://ntfy.sh
  AFON_NTFY_TOPIC    REQUIRED — same topic as the edge .env
  AFON_TICKER_HOST   default 127.0.0.1   (bind 0.0.0.0 only behind a firewall/tunnel)
  AFON_TICKER_PORT   default 8770
  AFON_TICKER_TOKEN  optional shared secret; if set, ingest requires `Authorization: Bearer …`
  AFON_TICKER_TZ     default Europe/Berlin
  AFON_TICKER_DB     default ./ticker_reminders.json  (recurring jobs persisted here)

HTTP API
--------
  GET  /health                      -> {"ok": true, "jobs": N}
  GET  /reminders                   -> [{"id","message","daily"}...]
  POST /reminders  {"message","daily":"HH:MM"[,"id"]}  -> {"id","message","daily"}
  POST /reminders/cancel  {"id"}    -> {"cancelled": true|false}

Run:  python3 afon_ticker.py   (or via the systemd unit — see install.sh)
"""

from __future__ import annotations

# --- maintenance guard --------------------------------------------------------------------------
# Mirrors afon/shared/maintenance.py, inlined because this script is deployed standalone and does
# not import the package. It is installed as a SYSTEM unit with Restart=always, and the VPS user has
# no passwordless sudo — so on 2026-08-13 there was no way for the owner to say "stay stopped" and
# have it stick. A file is: `rm ~/.afon/MAINTENANCE` un-parks it.
# Placement matters: this must sit AFTER `from __future__`, which has to be the first statement in
# the file. The first attempt at this guard went above it and left the ticker crash-looping on a
# SyntaxError — parked, but for the wrong reason and with a broken file on the host.
import pathlib as _pl  # noqa: E402
import sys as _sys  # noqa: E402

_LOCK = _pl.Path.home() / ".afon" / "MAINTENANCE"
if _LOCK.exists():
    print(f"afon ticker: parked by {_LOCK}", file=_sys.stderr)
    raise SystemExit(0)
# -------------------------------------------------------------------------------------------------

import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

NTFY_SERVER = os.environ.get("AFON_NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("AFON_NTFY_TOPIC", "")
HOST = os.environ.get("AFON_TICKER_HOST", "127.0.0.1")
PORT = int(os.environ.get("AFON_TICKER_PORT", "8770"))
TOKEN = os.environ.get("AFON_TICKER_TOKEN", "")
TZ = os.environ.get("AFON_TICKER_TZ", "Europe/Berlin")
DB = Path(os.environ.get("AFON_TICKER_DB", "ticker_reminders.json"))

_LOCK = threading.Lock()


def _push(message: str, title: str = "Reminder") -> None:
    if not NTFY_TOPIC:
        print("[ticker] no NTFY topic set; cannot push:", message)
        return
    try:
        httpx.post(
            f"{NTFY_SERVER}/{NTFY_TOPIC}",
            content=message.encode("utf-8"),
            headers={"Title": title},
            timeout=20,
        ).raise_for_status()
        print(f"[ticker] pushed: {message!r}")
    except Exception as e:  # noqa: BLE001
        print(f"[ticker] push failed: {type(e).__name__}: {e}")


def _parse_hhmm(s: str) -> tuple[int, int]:
    parts = s.strip().split(":")
    return int(parts[0]), (int(parts[1]) if len(parts) > 1 else 0)


def _load() -> list[dict]:
    if DB.exists():
        try:
            return json.loads(DB.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return []
    return []


def _save(items: list[dict]) -> None:
    DB.write_text(json.dumps(items, indent=2), encoding="utf-8")


class Ticker:
    def __init__(self) -> None:
        self.sched = BackgroundScheduler(timezone=TZ)

    def start(self) -> None:
        self.sched.start()
        for item in _load():  # rehydrate persisted recurring reminders on boot
            self._schedule(item["id"], item["message"], item["daily"])
        print(f"[ticker] started; {len(self.sched.get_jobs())} recurring reminder(s) loaded")

    def _schedule(self, jid: str, message: str, daily: str) -> None:
        hh, mm = _parse_hhmm(daily)
        self.sched.add_job(
            _push, trigger=CronTrigger(hour=hh, minute=mm, timezone=TZ),
            args=[message], id=jid, replace_existing=True,
        )

    def add(self, message: str, daily: str, jid: str | None = None) -> dict:
        jid = jid or uuid.uuid4().hex[:12]
        with _LOCK:
            self._schedule(jid, message, daily)
            items = [i for i in _load() if i["id"] != jid]
            items.append({"id": jid, "message": message, "daily": daily})
            _save(items)
        return {"id": jid, "message": message, "daily": daily}

    def cancel(self, jid: str) -> bool:
        with _LOCK:
            try:
                self.sched.remove_job(jid)
            except Exception:  # noqa: BLE001
                pass
            items = _load()
            kept = [i for i in items if i["id"] != jid]
            _save(kept)
            return len(kept) != len(items)

    def list(self) -> list[dict]:
        return _load()


TICKER = Ticker()


class Handler(BaseHTTPRequestHandler):
    def _authed(self) -> bool:
        if not TOKEN:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {TOKEN}"

    def _send(self, code: int, body: dict | list) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length", "0") or "0")
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def log_message(self, *_a) -> None:  # quiet default access logging
        pass

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send(200, {"ok": True, "jobs": len(TICKER.sched.get_jobs())})
        elif self.path == "/reminders":
            self._send(200, TICKER.list())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authed():
            self._send(401, {"error": "unauthorized"})
            return
        body = self._read_json()
        if self.path == "/reminders":
            message = (body.get("message") or "").strip()
            daily = (body.get("daily") or "").strip()
            if not message or not daily:
                self._send(400, {"error": "need message + daily 'HH:MM'"})
                return
            self._send(200, TICKER.add(message, daily, body.get("id")))
        elif self.path == "/reminders/cancel":
            self._send(200, {"cancelled": TICKER.cancel((body.get("id") or "").strip())})
        else:
            self._send(404, {"error": "not found"})


def main() -> None:
    if not NTFY_TOPIC:
        print("WARNING: AFON_NTFY_TOPIC is unset — the ticker can schedule but can't push.")
    TICKER.start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"[ticker] HTTP listening on http://{HOST}:{PORT}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
