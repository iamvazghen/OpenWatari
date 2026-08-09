"""C2 — inbound integration webhooks (HMAC-verified) → the proactive world-model.

Real push, not polling: Stripe / GitHub / Gmail (or anything) POST to ``/webhook/<source>`` on the
brain's HTTP sidecar. We verify an HMAC-SHA256 signature over the RAW body against a shared secret
(``AFON_WEBHOOK_SECRET``) in constant time, then fold a one-line summary into ``WORLD.note_event`` so
the anticipation reasoner sees it (e.g. "Stripe: payout of €420 cleared"). An unsigned/badly-signed
request is rejected — the world-model never ingests an unauthenticated event.

Signature header per source (all carry ``sha256=<hex>``):
  * GitHub → ``X-Hub-Signature-256`` (GitHub's own scheme)
  * others → ``X-Afon-Signature`` (set it on the Stripe/Gmail/Zapier side)
"""
from __future__ import annotations

import hashlib
import hmac
import json

from loguru import logger

from afon.config import settings

SOURCES = ("stripe", "github", "gmail")


def _expected_sig(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(secret: str | None, body: bytes, sig_header: str | None) -> bool:
    """Constant-time HMAC-SHA256 check of the raw body. False if no secret/sig configured or mismatch."""
    if not secret or not sig_header:
        return False
    return hmac.compare_digest(_expected_sig(secret, body), sig_header.strip())


def _summarize(source: str, payload: dict) -> str:
    """A short human line for the world-model. Best-effort per source; falls back to a generic note."""
    try:
        if source == "stripe":
            t = payload.get("type", "event")
            obj = (payload.get("data") or {}).get("object") or {}
            amt = obj.get("amount") or obj.get("amount_paid") or obj.get("amount_total")
            cur = (obj.get("currency") or "").upper()
            money = f" {amt/100:.2f} {cur}".rstrip() if isinstance(amt, (int, float)) else ""
            return f"Stripe: {t}{money}".strip()
        if source == "github":
            repo = ((payload.get("repository") or {}).get("full_name")) or "a repo"
            if "action" in payload and "pull_request" in payload:
                return f"GitHub: PR {payload['action']} on {repo}"
            if "commits" in payload:
                return f"GitHub: {len(payload['commits'])} push(es) to {repo}"
            if (payload.get("workflow_run") or {}).get("conclusion"):
                return f"GitHub: CI {payload['workflow_run']['conclusion']} on {repo}"
            return f"GitHub: event on {repo}"
        if source == "gmail":
            return f"Gmail: {payload.get('summary') or 'new mail activity'}"
    except Exception:  # noqa: BLE001 — a summary hiccup must never drop the event
        pass
    return f"{source}: event received"


def handle_webhook(source: str, body: bytes, sig_header: str | None,
                   *, world=None) -> tuple[int, str]:
    """Verify + record one webhook. Returns (http_status, message). No exception escapes.
    ``world`` is injectable for tests; defaults to the process WORLD singleton."""
    source = (source or "").strip().lower()
    if source not in SOURCES:
        return 404, f"unknown webhook source '{source}'"
    if not verify(settings.webhook_secret, body, sig_header):
        logger.warning(f"webhook '{source}' rejected: bad/missing HMAC signature")
        return 401, "invalid signature"
    try:
        payload = json.loads(body.decode() or "{}")
        if not isinstance(payload, dict):
            payload = {}
    except Exception:  # noqa: BLE001 — malformed JSON still counts as a (verified) event
        payload = {}
    note = _summarize(source, payload)
    try:
        if world is None:
            from afon.brain.world_model import WORLD as world
        world.note_event(note, ttl_hours=48.0)
        logger.info(f"webhook '{source}' verified → world-model: {note}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"webhook '{source}' verified but note_event failed: {type(e).__name__}")
        return 200, "accepted (note deferred)"
    return 200, note
