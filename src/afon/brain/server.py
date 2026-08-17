"""afon-brain WebSocket server — hosts ``AfonAgent`` over ``shared/protocol.py``.

This is the brain endpoint the ``BrainBridge`` comment promises: a remote client (the
iPhone web client in ``clients/iphone/``, the Mentra glasses bridge, or the laptop edge)
connects over WebSocket, sends ``Hello`` + ``Utterance``, and receives streamed
``StreamEvent`` reply chunks for incremental TTS.

ONE shared ``AfonAgent`` backs every connection, so all four devices talk to the SAME
brain + memory (the "same brain/session from phone and glasses" acceptance criterion).
Turns are serialised with a lock so a phone turn and a glasses turn can't interleave the
agent's history. A new utterance (or an explicit ``Barge``) cancels the session's in-flight
turn so Afon stops talking and listens.

Run it (also serves the phone client over HTTP)::

    uv run python -m afon.brain.server

For a phone on the same Wi-Fi, bind to all interfaces: ``AFON_BRAIN_HOST=0.0.0.0``.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from typing import Union
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from loguru import logger

from afon.brain.agent import AfonAgent
from afon.config import settings
from afon.shared.protocol import Barge, ErrorReport, Hello, StreamEvent, StreamKind, Utterance

ClientMessage = Union[Hello, Utterance, Barge, ErrorReport]

# Split a reply into sentence-ish chunks so the client speaks incrementally instead of
# waiting for the whole paragraph (lower perceived latency).
_SENTENCE_RE = re.compile(r".*?[.!?](?:\s|$)|.+$", re.DOTALL)


def chunk_for_tts(text: str) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    return [m.group(0).strip() for m in _SENTENCE_RE.finditer(text) if m.group(0).strip()]


def parse_client_message(raw: str | bytes) -> ClientMessage | None:
    """Parse one inbound frame into a typed protocol message (or None to ignore it)."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    kind = data.get("type")
    try:
        if kind == "hello":
            return Hello(**data)
        if kind == "utterance":
            return Utterance(**data)
        if kind == "barge":
            return Barge(**data)
        if kind == "error":
            return ErrorReport(**data)
    except Exception:  # noqa: BLE001 — a malformed frame must never kill the connection
        return None
    return None


class BrainServer:
    """Hosts a single shared ``AfonAgent`` and drives the protocol for every client."""

    def __init__(self, agent: AfonAgent | None = None) -> None:
        self._agent = agent or AfonAgent()
        self._lock = asyncio.Lock()                    # serialise turns over the shared brain
        self._turns: dict[str, asyncio.Task] = {}      # session_id -> in-flight turn task
        self._sessions: dict[str, dict] = {}           # session_id -> device/headphones info
        self._conns: dict[str, object] = {}            # session_id -> live websocket (for push)
        self._active_sid: str | None = None            # Phase 5.2 handoff: the device last spoken to
        self._tg_bridge = None                         # set in serve(): proactive VOICE to the phone
        self._proactive = None                         # set in serve(): the ProactiveEngine (feedback)

    def _speak_targets(self) -> list[tuple[str, object]]:
        """Devices to deliver an unprompted line to (Phase 5.2 — device handoff).

        Prefer the ACTIVE device: the one the owner last spoke to. So a nudge/reminder follows him to
        wherever he just was — the laptop he's typing at, the phone he just asked something on — instead
        of blurting from every connected client at once (or a phone in his pocket). Falls back to every
        connected device if the active one has since dropped, so reach is never reduced by the handoff.
        """
        if self._active_sid and self._active_sid in self._conns:
            return [(self._active_sid, self._conns[self._active_sid])]
        return list(self._conns.items())

    async def warmup(self) -> None:
        await self._agent.warmup()

    # ---- proactive (Phase 10): speak unprompted to listening devices, else ntfy --------
    @property
    def is_listening(self) -> bool:
        """A device is connected and could hear an unprompted interjection right now."""
        return bool(self._conns)

    async def _broadcast_assistant(self, message: str) -> int:
        """Speak ``message`` to every connected client (no push fallback). Returns clients reached.

        Interrupts any in-flight turn first so an unprompted line (proactive nudge or a fired
        reminder) can be heard immediately.
        """
        # Record what Afon says unprompted into the shared history, so when the owner replies to a
        # proactive nudge or a fired reminder the brain knows what he's responding to.
        try:
            self._agent.note_proactive(message)
        except Exception:  # noqa: BLE001
            pass
        sent = 0
        for sid, ws in self._speak_targets():  # Phase 5.2: follow the owner to his active device
            try:
                self._cancel(sid)  # interrupt any in-flight turn so he can speak now
                await self._send(
                    ws,
                    StreamEvent(
                        session_id=sid, kind=StreamKind.assistant, delta=message + " ", final=True
                    ),
                )
                sent += 1
            except Exception:  # noqa: BLE001 — a dead socket shouldn't block the rest
                continue
        return sent

    async def proactive_emit(self, message: str, urgency: float, speak: bool) -> str:
        """Emitter for the proactive engine: voice to connected clients, or a phone push."""
        if speak and await self._broadcast_assistant(message):
            return "voice"
        # Nobody listening (or send failed): prefer a Telegram VOICE NOTE to the phone (proactive
        # voice, 24/7, laptop-independent), then fall back to an ntfy text push.
        if self._tg_bridge is not None:
            try:
                if await self._tg_bridge.send_proactive(message):
                    return "voice-note"
            except Exception:  # noqa: BLE001
                pass
        from afon.brain.tools.notify import push

        ok = await push(message, title="Afon")
        return "push" if ok else "suppressed"

    def speak_reminder(self, message: str) -> None:
        """``on_speak`` hook for the scheduler: speak a fired reminder to connected clients.

        Sync (the scheduler calls it inside ``_fire`` on this loop); schedules the async broadcast.
        Phone push is NOT done here — ``scheduler._fire`` owns that via ``push_phone`` so a reminder
        isn't pushed twice. If no client is connected this is a no-op and ntfy still delivers.
        """
        try:
            asyncio.get_running_loop().create_task(
                self._broadcast_assistant(f"Reminder, sir: {message}")
            )
        except Exception:  # noqa: BLE001
            logger.warning("speak_reminder: no running loop to schedule the broadcast")

    @staticmethod
    async def _send(ws, ev: StreamEvent) -> None:
        await ws.send(ev.model_dump_json())

    async def handle_voice_request(
        self, body: bytes, content_type: str
    ) -> tuple[bytes | None, str, str]:
        """One-shot voice turn for the iPhone Siri Shortcut (no page, no WebSocket).

        Accepts EITHER a JSON body ``{"text": "..."}`` (iOS on-device dictation — fast, the
        default the Shortcut uses) OR a raw audio clip (``audio/*`` — recorded audio, transcribed
        here with Deepgram). Runs one turn through the SHARED agent under the same lock as voice and
        Telegram, then returns Afon's reply as **MP3 audio** (``audio/mpeg``) which the Shortcut
        plays. Returns ``(audio_bytes_or_None, reply_text, transcript)``; on synth failure the caller
        sends the text so Siri can read it aloud.
        """
        from afon.brain.voice_io import synthesize, transcribe_audio

        ct = (content_type or "").split(";")[0].strip().lower()
        text = ""
        if ct == "application/json":
            try:
                text = (json.loads(body or b"{}").get("text") or "").strip()
            except (json.JSONDecodeError, TypeError, AttributeError):
                text = ""
        elif ct.startswith("audio/") or ct == "application/octet-stream":
            text = await transcribe_audio(body, content_type=ct or "audio/m4a")
        if not text:
            return None, "I didn't catch anything, sir.", ""

        async with self._lock:
            try:
                reply = await self._agent.respond(text)
            except Exception as e:  # noqa: BLE001 — never 500 the phone; always speak something
                logger.warning(f"/talk turn failed: {type(e).__name__}: {e}")
                reply = ("I'm having trouble reaching my reasoning right now, sir — "
                         "give me a moment and try again.")
        audio = await synthesize(reply)
        return audio, reply, text

    async def handle_message(self, ws, msg: ClientMessage) -> None:
        if isinstance(msg, Hello):
            self._sessions[msg.session_id] = {
                "device_id": msg.device_id,
                "headphones_connected": msg.headphones_connected,
            }
            self._conns[msg.session_id] = ws   # track the socket for unprompted interjections
            logger.info(
                f"hello session={msg.session_id} device={msg.device_id} "
                f"headphones={msg.headphones_connected}"
            )
            await self._send(
                ws, StreamEvent(session_id=msg.session_id, kind=StreamKind.lifecycle, delta="ready")
            )
            return

        if isinstance(msg, Barge):
            self._cancel(msg.session_id)
            await self._send(
                ws,
                StreamEvent(session_id=msg.session_id, kind=StreamKind.lifecycle, delta="cancelled"),
            )
            return

        if isinstance(msg, ErrorReport):
            # A failure that happened on the owner's laptop (edge or pc_agent). Store it in THIS
            # host's journal, keeping its original host/process, so one query here sees everything
            # the owner experienced instead of only the server's half.
            from afon.shared import errors as _err

            e = msg.entry if isinstance(msg.entry, dict) else {}
            _err.record(
                level=str(e.get("level", "ERROR")), subsystem=str(e.get("subsystem", "laptop")),
                message=str(e.get("message", "")), error_type=str(e.get("type", "")),
                where=str(e.get("where", "")), turn=str(e.get("turn", "")),
                process=str(e.get("process", "edge")), host=str(e.get("host", "laptop")),
                context=e.get("context") if isinstance(e.get("context"), dict) else None,
                ship=False,   # already home; re-shipping would loop
            )
            return

        if isinstance(msg, Utterance):
            self._active_sid = msg.session_id  # Phase 5.2 handoff: this is now the owner's live device
            self._cancel(msg.session_id)  # a new utterance supersedes the previous turn
            self._turns[msg.session_id] = asyncio.create_task(self._run_turn(ws, msg))
            return

    def _cancel(self, session_id: str) -> None:
        task = self._turns.get(session_id)
        if task and not task.done():
            task.cancel()

    async def _daily_digest_addendum(self, digest_task, now) -> str:
        """Resolve the first-turn daily catch-up: if a build was kicked off for this (first-of-day)
        turn, await it, mark the 'edge' channel delivered, and return a spoken 'By the way, sir — …'
        addendum (or '' if nothing / not due). Once per day, persisted, so it never repeats. Fail-quiet."""
        if digest_task is None:
            return ""
        try:
            from afon.brain import daily_digest

            body = await digest_task
            daily_digest.mark_delivered("edge", now)  # mark on the first turn regardless of content
            if not body:
                return ""
            line = "By the way, sir — " + body + "."
            try:
                self._agent.note_proactive(line)  # so a follow-up ('mark the rent one done') has context
            except Exception:  # noqa: BLE001
                pass
            return line
        except Exception as e:  # noqa: BLE001
            logger.warning(f"daily digest addendum failed: {e}")
            return ""

    def _grade_proactive_reaction(self, text: str, now) -> None:
        """If Afon just interjected, read the owner's reply as accept/dismiss so the engine learns
        (Phase 1 feedback). Only clear yes/no moves the needle; ambiguity is left neutral. Fail-quiet."""
        if self._proactive is None:
            return
        try:
            from afon.brain.proactive import classify_reaction

            kind = self._proactive.pending_feedback(now)
            if not kind:
                return
            verdict = classify_reaction(text)
            if verdict == "positive":
                self._proactive.record_feedback(kind, "act", now)
            elif verdict == "negative":
                self._proactive.record_feedback(kind, "dismiss", now)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"proactive reaction grading skipped: {e}")

    async def _run_turn(self, ws, utt: Utterance) -> None:
        sid = utt.session_id
        # Adopt the edge's correlation id so every brain log line and journal entry for this turn
        # shares one key with the laptop's. This runs inside the turn's own task, and contextvars
        # are per-task, so concurrent sessions can't bleed ids into each other.
        from afon.shared import errors as _err

        _err.new_turn(utt.turn_id or None)
        # On the owner's FIRST live-edge turn of the day, build the daily catch-up CONCURRENTLY with
        # the reply (so it adds no latency) and append it once the reply is done. `due` is a cheap
        # file read; the network build only starts when it's actually the first turn today.
        now = datetime.now(ZoneInfo(settings.user_tz))
        # If he's replying to a just-made interjection, let the engine learn from his reaction.
        self._grade_proactive_reaction(utt.text, now)
        digest_task = None
        try:
            from afon.brain import daily_digest

            if daily_digest.due("edge", now):
                digest_task = asyncio.create_task(daily_digest.build_body())
        except Exception:  # noqa: BLE001
            digest_task = None
        try:
            await self._send(
                ws, StreamEvent(session_id=sid, kind=StreamKind.lifecycle, delta="thinking")
            )
            loop = asyncio.get_running_loop()

            def progress(note: str) -> None:  # tool fillers -> spoken progress
                loop.create_task(
                    self._send(ws, StreamEvent(session_id=sid, kind=StreamKind.tool, delta=note))
                )

            # Stream sentence-by-sentence as the agent generates them, so a remote client (phone,
            # glasses, or the unified laptop edge) starts speaking sentence 1 while the rest is still
            # being written — same low-latency behaviour as the in-process edge path. We send the
            # PREVIOUS sentence as non-final and only mark the last one final=True, so the client
            # knows when the turn is complete.
            async with self._lock:
                # Send EACH sentence the instant it's generated (final=False), then a terminal empty
                # final=True marker. The old code held one sentence back (so it could tag the last as
                # final), which delayed the FIRST spoken sentence by a whole extra sentence's generation
                # — the dominant streaming lag on multi-sentence replies. Clients speak deltas as they
                # arrive and use final only as the end-of-turn signal (an empty delta speaks nothing).
                async for sentence in self._agent.respond_stream(utt.text, on_progress=progress):
                    await self._send(ws, StreamEvent(
                        session_id=sid, kind=StreamKind.assistant, delta=sentence + " ", final=False))
                # First-turn-of-the-day catch-up, appended once per day before the terminal marker.
                addendum = await self._daily_digest_addendum(digest_task, now)
                if addendum:
                    await self._send(ws, StreamEvent(
                        session_id=sid, kind=StreamKind.assistant, delta=addendum + " ", final=False))
                await self._send(ws, StreamEvent(
                    session_id=sid, kind=StreamKind.assistant, delta="", final=True))
        except asyncio.CancelledError:
            # Barge-in or a superseding utterance: tell the client to stop speaking.
            try:
                await self._send(
                    ws,
                    StreamEvent(
                        session_id=sid, kind=StreamKind.lifecycle, delta="cancelled", final=True
                    ),
                )
            except Exception:  # noqa: BLE001
                pass
            raise
        except Exception:  # noqa: BLE001
            logger.exception("brain turn failed")
            await self._send(
                ws,
                StreamEvent(session_id=sid, kind=StreamKind.lifecycle, delta="error", final=True),
            )

    async def _control_handler(self, ws) -> None:
        """The laptop's PC-control executor (edge/pc_agent.py) connects here so the brain can run
        commands on the laptop. One executor at a time; results come back as pc_result frames."""
        from afon.brain.pc_link import PC_LINK

        PC_LINK.register(ws)
        logger.info("pc-control: laptop executor connected")
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if data.get("type") == "pc_hello":
                    PC_LINK.register(ws, data.get("host"))
                    logger.info(f"pc-control: laptop '{data.get('host')}' ready (executor {data.get('ver', '?')})")
                elif data.get("type") == "pc_result":
                    PC_LINK.resolve(data.get("id", ""), bool(data.get("ok")), str(data.get("output", "")))
                elif data.get("type") == "pc_error":
                    # A failure inside the laptop executor (camera, PC control, file ops). Those run
                    # on the owner's machine, so without this they would never appear in the brain's
                    # journal — the half of his experience the server can't see directly.
                    from afon.shared import errors as _err

                    e = data.get("entry") if isinstance(data.get("entry"), dict) else {}
                    _err.record(
                        level=str(e.get("level", "ERROR")),
                        subsystem=str(e.get("subsystem", "pc_agent")),
                        message=str(e.get("message", "")), error_type=str(e.get("type", "")),
                        where=str(e.get("where", "")), turn=str(e.get("turn", "")),
                        process=str(e.get("process", "pc_agent")),
                        host=str(e.get("host", "laptop")),
                        context=e.get("context") if isinstance(e.get("context"), dict) else None,
                        ship=False,
                    )
        except Exception:  # noqa: BLE001
            pass
        finally:
            PC_LINK.unregister(ws)
            logger.info("pc-control: laptop executor disconnected")

    async def handler(self, ws) -> None:
        if not self._authorized(ws):
            await ws.close(code=4401, reason="unauthorized")
            return
        # Route the laptop PC-control executor to its own handler (path /control).
        try:
            from urllib.parse import urlparse as _u
            if _u(getattr(ws.request, "path", "")).path.rstrip("/").endswith("/control"):
                await self._control_handler(ws)
                return
        except Exception:  # noqa: BLE001
            pass
        logger.info("client connected")
        try:
            async for raw in ws:
                msg = parse_client_message(raw)
                if msg is not None:
                    await self.handle_message(ws, msg)
        except Exception:  # noqa: BLE001
            pass
        finally:
            # Drop any sessions bound to this socket so we don't push into a dead connection.
            for sid in [s for s, w in self._conns.items() if w is ws]:
                self._conns.pop(sid, None)
                if self._active_sid == sid:      # handoff: the active device left; fall back to broadcast
                    self._active_sid = None
            logger.info("client disconnected")

    @staticmethod
    def _authorized(ws) -> bool:
        # Loopback dev: leave api_auth_token blank = no auth. Set it when binding to 0.0.0.0.
        if not settings.api_auth_token:
            return True
        expected = settings.api_auth_token
        # 1) Header (native/edge clients send `Authorization: Bearer <token>`).
        try:
            if ws.request.headers.get("Authorization", "") == f"Bearer {expected}":
                return True
        except Exception:  # noqa: BLE001
            pass
        # 2) Query string (BROWSERS cannot set a WS Authorization header, so the phone client
        #    connects to ws://host:8765/voice?token=<token>). Same secret, different channel.
        try:
            from urllib.parse import parse_qs, urlparse

            q = parse_qs(urlparse(getattr(ws.request, "path", "")).query)
            if (q.get("token") or [""])[0] == expected:
                return True
        except Exception:  # noqa: BLE001
            pass
        return False


async def serve(host: str | None = None, port: int | None = None) -> None:
    """Run the brain WebSocket server until cancelled."""
    import websockets

    # Error tracking for the brain: the same structured journal the laptop writes, plus the asyncio
    # handler that catches the many fire-and-forget tasks here (proactive ticks, backlog workers,
    # scheduler jobs) whose failures previously went only to stderr.
    try:
        from afon.shared import errors as _err

        _err.install("brain")
        _err.install_asyncio_handler(asyncio.get_running_loop())
    except Exception as e:  # noqa: BLE001 — never block the brain on observability
        logger.warning(f"error tracking unavailable: {type(e).__name__}: {e}")

    # One brain only. systemd normally guarantees this, but a manual run alongside the service (or a
    # restart overlapping a slow shutdown) gives two brains both ticking the proactive engine and
    # double-sending to the owner. Newest wins, same rule as the laptop daemons.
    try:
        from afon.shared import singleton

        if not singleton.claim("brain"):
            logger.warning("brain: a newer instance already owns this role — exiting")
            return
    except Exception as e:  # noqa: BLE001 — fail open
        logger.warning(f"singleton guard unavailable: {type(e).__name__}: {e}")

    server = BrainServer()
    await server.warmup()
    host = host or settings.brain_host
    port = port or settings.brain_port
    path = urlparse(settings.brain_ws_url).path or "/voice"
    logger.info(f"afon-brain listening on ws://{host}:{port}{path}")

    # HTTP sidecar (health + phone web client + the iPhone Siri-Shortcut /talk endpoint). Started
    # here, INSIDE the running loop, so the threaded HTTP handler can drive the shared async agent.
    _serve_client_http(host, settings.client_http_port, server, asyncio.get_running_loop())

    # Phase 4 — reminder scheduler. In brain-server (24/7) mode the BRAIN owns the SQLite jobstore,
    # so reminders fire even with no laptop edge connected: a fired reminder is spoken to any
    # connected client (server.speak_reminder), and scheduler._fire independently pushes it to the
    # phone via ntfy. Exactly one process must own the jobstore — run the brain server OR the
    # fully-local edge (which starts its own scheduler), not both against the same DB.
    from afon.brain.scheduler import SCHEDULER

    SCHEDULER.start(on_speak=server.speak_reminder)
    logger.info("reminder scheduler started (brain owns the jobstore)")

    # Daily consolidated catch-up (past-due tasks + important email), delivered once at this time.
    # Scheduled whenever a briefing time is set — the digest pulls from Notion AND Gmail AND the
    # local task queue, so it's useful even if only one of those is configured. Delivery uses the
    # richest proactive path: speak to a listening device, else Telegram voice note, else ntfy push.
    if settings.task_briefing_time:
        SCHEDULER.schedule_daily_briefing(settings.task_briefing_time)

    # Phase 3.1 — autonomous daily BACKLOG pass (opt-in: AFON_BACKLOG_ENABLED). When on and a tasks
    # DB is set, a daily job has the bounded worker attempt overdue/inbox tasks (safe work only;
    # outward steps deferred) and comments the results back onto each Notion task.
    if settings.backlog_enabled and settings.notion_tasks_db_id:
        SCHEDULER.set_backlog_runner(server._agent.run_backlog)
        if settings.proactive_action_reports:
            # Report the pass unprompted (what + why + reasoning) so an autonomous action is never silent.
            SCHEDULER.set_backlog_reporter(server._agent.backlog_report)
        SCHEDULER.schedule_daily_backlog(settings.backlog_time)
        logger.info("autonomous daily backlog pass scheduled")

    # Phase 4.1 — autonomous daily OBJECTIVES advance (opt-in: AFON_OBJECTIVES_ENABLED). A daily job
    # advances the objectives the owner handed Afon to drive, one safe step each, and reports the
    # progress unprompted. Safe by construction (the worker defers every outward step).
    if settings.objectives_enabled:
        SCHEDULER.set_objectives_runner(server._agent.advance_objectives)
        SCHEDULER.schedule_daily_objectives(settings.objectives_time)
        logger.info("autonomous daily objectives advance scheduled")

    # Daily backup of Afon's L1/L2 memory (the one durable store with no other automated backup).
    SCHEDULER.schedule_daily_backup()

    # P1 #6 — memory hygiene. A daily job dedups near-identical learned facts, caps the active set,
    # and rotates old journals so 24/7 accumulation doesn't dull recall or re-bloat the prompt.
    try:
        from afon.brain.maintenance import schedule_maintenance

        schedule_maintenance(SCHEDULER)
    except Exception as e:  # noqa: BLE001 — hygiene is best-effort, never blocks startup
        logger.warning(f"memory hygiene not scheduled: {e}")

    # T12 — pre-fetch the full Composio catalog at startup so the system prompt has tool
    # awareness from the first turn. Fire-and-forget: do not block startup on the network.
    # NOTE: rely on the module-level `asyncio` import (no local rebind — that would shadow
    # the later `asyncio.get_running_loop()` call in serve() and trip UnboundLocalError).
    try:
        from afon.brain.composio_catalog import refresh, invalidate

        async def _refresh_now() -> None:
            try:
                await refresh()
                invalidate()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"composio catalog initial refresh failed (will retry nightly): {e}")
        try:
            asyncio.get_running_loop().create_task(_refresh_now())
        except RuntimeError:
            asyncio.run(_refresh_now())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"composio catalog startup refresh could not be scheduled: {e}")

    # T3b — daily pattern scan; T3c — weekly memory review (both fail-quiet like hygiene).
    try:
        SCHEDULER.schedule_pattern_scan()
        SCHEDULER.schedule_weekly_review()
        SCHEDULER.schedule_restore_drill()   # 22.F4 — an untested backup is a rumour
    except Exception as e:  # noqa: BLE001
        logger.warning(f"pattern scan / weekly review / restore drill not scheduled: {e}")

    # T10 — reliability health probe every 4h (silent on green, ntfy on red).
    try:
        from apscheduler.triggers.interval import IntervalTrigger
        from afon.brain._scheduled_jobs import _fire_reliability_probe

        sched = SCHEDULER._ensure()  # noqa: SLF001 — scheduler is single-instance
        sched.add_job(_fire_reliability_probe, trigger=IntervalTrigger(hours=4),
                      id="reliability-health-probe", name="reliability health probe",
                      misfire_grace_time=7200, coalesce=True, replace_existing=True)
        logger.info("reliability health probe scheduled every 4h")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"reliability probe not scheduled: {e}")

    # T12 — nightly Composio catalog refresh (03:15, before memory backup, so the morning briefing
    # sees any new tools added overnight). Triggers a `_fire_composio_catalog_refresh` task.
    try:
        from afon.brain._scheduled_jobs import _fire_composio_catalog_refresh
        from apscheduler.triggers.cron import CronTrigger

        sched = SCHEDULER._ensure()
        sched.add_job(_fire_composio_catalog_refresh,
                      trigger=CronTrigger(hour=3, minute=15),
                      id="composio-catalog-refresh", name="composio catalog refresh",
                      misfire_grace_time=3600, coalesce=True, replace_existing=True)
        logger.info("composio catalog refresh scheduled nightly at 03:15")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"composio catalog refresh not scheduled: {e}")

    # Phase 0 companion — activity/presence poller: sample the laptop's foreground window so Afon
    # knows what the owner is doing (context for proactivity) + can report screen-time. Local-only,
    # a harmless no-op when no laptop is attached or tracking is paused.
    from afon.brain.presence import PRESENCE

    PRESENCE.start()
    logger.info("presence/activity poller started")

    # Phase 10 — proactive companion. Off unless AFON_PROACTIVE_ENABLED=true; when on, a
    # background tick may speak to listening clients (or push) within its budget + quiet hours.
    engine = None
    if settings.proactive_enabled:
        from pathlib import Path

        from afon.brain.proactive import ProactiveEngine, default_signal_sources

        # Persist suppression + budget so a restart doesn't re-fire nudges. In the state root with
        # everything else Afon writes (30.F1) — it used to be derived from the tasks DB's parent,
        # so moving the task queue silently moved this too.
        from afon.shared.paths import state_dir

        state_path = settings.proactive_state_path or str(state_dir() / "proactive_state.json")
        engine = ProactiveEngine(
            emit=server.proactive_emit,
            sources=default_signal_sources(),
            is_listening=lambda: server.is_listening,
            is_busy=lambda: PRESENCE.busy(),   # Phase 1: hold routine nudges while he's heads-down
            state_path=state_path,
        )
        engine.start()
        server._proactive = engine  # so a live turn can grade his reaction to the last interjection

    # 24/7 reachability — inbound Telegram. DM the bot from any device (even with the laptop off);
    # the shared agent answers, so Telegram and voice share one memory + conversation context.
    from afon.brain.telegram_bridge import TelegramBridge

    async def _telegram_respond(text: str) -> str:
        async with server._lock:                      # serialise with voice turns over the shared brain
            return await server._agent.respond(text)

    tg_bridge = TelegramBridge(_telegram_respond, settings.telegram_default_chat)
    server._tg_bridge = tg_bridge   # proactive VOICE to the phone (proactive_emit phone fallback)
    # Now that proactive delivery (incl. the Telegram voice-note fallback) is ready, let the daily
    # task briefing use it: speak to a listening device, else voice note, else push.
    SCHEDULER.set_briefing_emit(server.proactive_emit)

    # Background task queue: when a long fleet job finishes, announce it by voice (with how long it
    # took + complexity + the result), via the same proactive channel — voice, else phone voice note.
    from afon.brain.tasks import TASKS

    async def _announce_task(t) -> None:
        if t.status == "done":
            dur = t.meta.get("duration", t.human_elapsed())
            grade = t.meta.get("complexity", "")
            head = f"Done, sir — '{t.title}' finished in {dur}"
            head += f" ({grade})." if grade else "."
            body = (t.result or "").strip()
            msg = head + (" " + body[:600] if body else "")
        else:
            msg = f"Sir, '{t.title}' didn't complete after {t.human_elapsed()}: {(t.result or '')[:200]}"
        await server.proactive_emit(msg, 0.7, True)

    TASKS.on_complete = _announce_task
    if tg_bridge.enabled:
        asyncio.create_task(tg_bridge.run())
        logger.info("telegram bridge started — message the bot to reach Afon anywhere, 24/7")

    # Keepalive must tolerate a turn that briefly blocks the event loop (LLM inference + a sync tool).
    # The default 20s ping_timeout dropped the link MID-REPLY whenever a turn ran long; 75s comfortably
    # outlives any real turn while still recycling a genuinely dead socket.
    # ponytail: timeout bump, not a fix for blocking-in-the-loop — make turns fully async if a turn can exceed 75s.
    async with websockets.serve(
        server.handler, host, port, max_size=4 * 1024 * 1024,
        ping_interval=20, ping_timeout=75,
    ):
        await asyncio.Future()  # run forever


def _serve_client_http(
    host: str, port: int, server: "BrainServer | None" = None, loop=None
) -> None:
    """Serve a liveness route + the phone web client (clients/) over HTTP in a background thread.

    ``GET /healthz`` returns 200 ``ok`` so systemd / a load balancer / a cron can check the brain
    is alive without opening a WebSocket (see deploy/vps/afon-brain.service). Always started even
    if the clients/ dir is missing, because the health check must work on a headless VPS.

    ``POST /talk`` is the iPhone Siri-Shortcut endpoint (no page, no WebSocket): an authenticated
    POST of JSON ``{"text": ...}`` (on-device dictation) or a raw audio clip returns Afon's reply
    as MP3 the Shortcut plays. ``server`` and ``loop`` are the running brain + its event loop, so
    the (threaded) HTTP handler can drive the shared async agent via ``run_coroutine_threadsafe``.
    """
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "clients"
    serve_dir = str(root) if root.exists() else None
    if serve_dir is None:
        logger.warning(f"client dir not found at {root}; serving /healthz only")

    def _post_authorized(handler) -> bool:
        expected = settings.api_auth_token
        if not expected:
            return True
        if handler.headers.get("Authorization", "") == f"Bearer {expected}":
            return True
        from urllib.parse import parse_qs, urlparse

        q = parse_qs(urlparse(handler.path).query)
        return (q.get("token") or [""])[0] == expected

    class _Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=serve_dir or str(Path.cwd()), **kw)

        def _route(self):
            # Path without the query string, no trailing slash — so '/iphone/?v=2' still routes.
            from urllib.parse import urlparse

            return urlparse(self.path).path.rstrip("/")

        def do_POST(self):  # noqa: N802 (http.server API) — the Siri Shortcut voice turn
            route = self._route()
            # C2: HMAC-verified inbound integration webhooks -> the proactive world-model. No owner
            # auth token here (the signature IS the auth); source-scoped under /webhook/<source>.
            if route.startswith("/webhook/"):
                from afon.brain.webhooks import handle_webhook
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                sig = (self.headers.get("X-Hub-Signature-256")
                       or self.headers.get("X-Afon-Signature"))
                status, msg = handle_webhook(route.split("/webhook/", 1)[1], body, sig)
                out = json.dumps({"status": "ok" if status == 200 else "rejected",
                                  "detail": msg}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
                return
            if route != "/talk":
                self.send_error(404, "not found")
                return
            if server is None or loop is None:
                self.send_error(503, "brain not ready")
                return
            if not _post_authorized(self):
                self.send_error(401, "unauthorized")
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            ct = self.headers.get("Content-Type", "application/json")
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    server.handle_voice_request(body, ct), loop
                )
                audio, reply, transcript = fut.result(timeout=180)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"/talk failed: {type(e).__name__}: {e}")
                self.send_error(500, "turn failed")
                return
            # Headers carry the text too, so the Shortcut can show/read it if audio is unavailable.
            def _h(s: str) -> str:
                return (s or "").replace("\n", " ").encode("ascii", "ignore").decode()[:900]

            from urllib.parse import parse_qs, urlparse as _u

            want_text = (parse_qs(_u(self.path).query).get("format") or [""])[0] == "text"
            if want_text or not audio:
                # Bulletproof path: return JSON {reply} for a Shortcut that uses "Speak Text"
                # (Siri's own voice) — works on every iOS version, no audio-file playback needed.
                body_out = json.dumps({"reply": reply, "transcript": transcript}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body_out)))
                self.end_headers()
                self.wfile.write(body_out)
                return
            if audio:
                self.send_response(200)
                self.send_header("Content-Type", "audio/mpeg")
                self.send_header("Content-Length", str(len(audio)))
                self.send_header("X-Afon-Reply", _h(reply))
                self.send_header("X-Afon-Transcript", _h(transcript))
                self.end_headers()
                self.wfile.write(audio)
            else:
                body_out = json.dumps({"reply": reply, "transcript": transcript}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body_out)))
                self.end_headers()
                self.wfile.write(body_out)

        def do_GET(self):  # noqa: N802 (http.server API)
            if self._route() in ("/healthz", "/health"):
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            # Structured observability (TODO 8.8): JSON metrics snapshot. Auth-gated (same bearer/
            # ?token as /talk) since it reveals usage patterns; on a loopback-only brain it's open.
            if self._route() == "/metrics":
                if not _post_authorized(self):
                    self.send_error(401, "unauthorized")
                    return
                import json as _json

                from afon.brain.metrics import METRICS
                body = _json.dumps(METRICS.snapshot(), indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            # Phase 5.3 — ambient HUD state (objectives / working-on / awaiting-approval / presence).
            # Auth-gated like /metrics (it reveals what he's doing); open on a loopback-only brain.
            if self._route() == "/hud.json":
                if not _post_authorized(self):
                    self.send_error(401, "unauthorized")
                    return
                import json as _json

                from afon.brain.hud import hud_snapshot
                body = _json.dumps(hud_snapshot(), indent=2).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if serve_dir is None:
                self.send_error(404, "no client dir on this host")
                return
            # Serve the phone client with the auth token injected, so a browser (which can't set a
            # WS Authorization header) connects with no manual token paste. The HTTP port is only
            # reachable on the trusted tailnet/localhost, so embedding the token here is acceptable.
            page = {"/iphone": ("iphone", "/iphone"), "/hud": ("hud", "/hud")}.get(self._route())
            if self._route() in ("/iphone/index.html", "/hud/index.html"):
                page = (self._route().split("/")[1], "/" + self._route().split("/")[1])
            if page is not None:
                subdir, _ = page
                idx = Path(serve_dir) / subdir / "index.html"
                if idx.exists():
                    html = idx.read_text(encoding="utf-8")
                    tok = (settings.api_auth_token or "").replace("</", "<\\/")
                    inject = f'<script>window.AFON_TOKEN="{tok}";</script>'
                    html = html.replace("</head>", inject + "</head>", 1)
                    body = html.encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()  # end_headers() below adds the no-cache headers
                    self.wfile.write(body)
                    return
            super().do_GET()

        def end_headers(self):
            # Never let Safari serve a stale cached copy of the client — a stale page silently hides
            # new UI (e.g. the mic button) until a manual hard refresh.
            if self._route().startswith("/iphone") or self._route().startswith("/hud"):
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
            super().end_headers()

        def log_message(self, *args):  # keep the brain log clean
            return

    httpd = ThreadingHTTPServer((host, port), _Handler)
    where = f"http://{host}:{port}"
    logger.info(f"brain health at {where}/healthz"
                + (f"; phone client at {where}/iphone/" if serve_dir else ""))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()


def main() -> None:
    from afon.shared.maintenance import halt_if_parked

    halt_if_parked("brain")   # systemd Restart=always cannot be argued with — see maintenance.py

    # 30.F1: before anything opens a store, make sure there is only one of each. A deploy untars
    # the source tree into the brain host's own directory, so state resolved against the repo root
    # forked per host and neither host could tell. This moves any such leftover into the state root
    # — it never overwrites, so the worst case is a report that two copies exist and a human runs
    # scripts/merge_memory.py. Running it here rather than expecting an operator to ssh in is what
    # makes a redeploy self-healing.
    from afon.shared.paths import migrate_repo_state, second_origins

    for line in migrate_repo_state():
        logger.warning(f"state migration: {line}")
    for stale in second_origins():
        logger.error(f"TWO MEMORIES: {stale} still holds state — merge it with "
                     f"scripts/merge_memory.py, or this host answers from whichever it opens first")

    host = settings.brain_host
    try:
        asyncio.run(serve(host=host))
    except KeyboardInterrupt:
        logger.info("afon-brain stopped")


if __name__ == "__main__":
    main()
