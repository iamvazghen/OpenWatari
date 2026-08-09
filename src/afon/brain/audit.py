"""Audit log — a tamper-evident trail of every tool action Afon takes (cross-cutting, Phase X).

Trust comes from being able to see what he did. Each tool call appends one JSON line to a daily
file under ``audit_log_dir`` (default ``<repo>/audit/YYYY-MM-DD.jsonl``): when, which tool, the
arguments, and a clipped result. **Secrets are never written** — argument keys that look like a
password / token / secret are redacted before the line is built (protocol passwords especially must
never land on disk).

It is best-effort and fail-quiet: a logging error must never break a turn, so every write is wrapped
and a failure is dropped (with a warning), not raised.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from afon.config import settings

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Argument keys whose values must be redacted before anything is written.
_SECRET_KEYS = ("password", "token", "secret", "api_key", "apikey", "auth", "credential")


def _secret_values() -> list[str]:
    """Actual secret VALUES from settings, to scrub from the whole line (defence in depth).

    Key-based redaction only catches obvious arg keys; this also removes a secret that leaks via a
    tool *result* (e.g. PowerShell echoing an env var) or an unexpectedly-named field.

    `*_password` fields are ALWAYS scrubbed (any length — even 4-digit codes count as secrets).
    Other secret-keyed fields are scrubbed only when long enough (>=6) to avoid mangling ordinary
    English words that happen to contain "secret" or "token" substrings.
    """
    vals: list[str] = []
    for name in getattr(settings, "model_fields", {}):
        v = getattr(settings, name, None)
        if not isinstance(v, str) or not v:
            continue
        if name.endswith("_password"):
            vals.append(v)   # ANY password — even short numeric codes
        elif any(s in name.lower() for s in _SECRET_KEYS) and len(v) >= 6:
            vals.append(v)
    return vals


def _audit_dir() -> Path:
    base = Path(settings.audit_log_dir) if settings.audit_log_dir else _REPO_ROOT / "audit"
    return base


def _redact(args: dict | None) -> dict:
    out: dict = {}
    for k, v in (args or {}).items():
        if any(s in k.lower() for s in _SECRET_KEYS):
            out[k] = "***redacted***"
        elif isinstance(v, str) and len(v) > 300:
            out[k] = v[:300] + "…"
        else:
            out[k] = v
    return out


def record(
    tool: str,
    args: dict | None,
    result: str,
    *,
    ok: bool = True,
    actor: str = "owner",
    decision: str = "allow",
    rule: str = "",
    reasoning: str = "",
) -> None:
    """Append one audit line for a tool call. Never raises.

    ``actor``/``decision``/``rule``/``reasoning`` were added 2026-08-09 after an independent audit
    (iFixAi B03, "audit records carry required fields") scored this log 0%. The original line held
    ``ts/tool/args/ok/result``, where ``ok`` means *the call succeeded* — not *the call was
    allowed*. Those are different questions, and only the second one matters after an incident:
    the log could not answer "who asked for this, and under what rule was it permitted?" A blocked
    action and a failed action were both simply ``ok: false``.

    Defaults keep every existing call site valid and truthful: Afon is single-principal, so an
    unattributed action is the owner's, and a call that reaches the audit line was not blocked.
    """
    try:
        now = datetime.now(timezone.utc)
        d = _audit_dir()
        d.mkdir(parents=True, exist_ok=True)
        line = {
            "ts": now.isoformat(),
            "tool": tool,
            "args": _redact(args),
            "ok": ok,
            "actor": actor,
            "decision": decision,
            "rule_applied": rule or ("confirm_gate:not_gated" if decision == "allow" else "unspecified"),
            "reasoning": reasoning,
            "result": (result or "")[:500],
        }
        payload = json.dumps(line, ensure_ascii=False)
        # Value-level scrub: remove any real secret that slipped into args or the result text.
        for sv in _secret_values():
            if sv in payload:
                payload = payload.replace(sv, "***redacted***")
        path = d / f"{now:%Y-%m-%d}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(payload + "\n")
    except Exception as e:  # noqa: BLE001 — auditing must never break a turn
        logger.warning(f"audit write failed: {type(e).__name__}: {e}")


def recent(limit: int = 10) -> list[dict]:
    """Return the most recent audit entries (today's file), newest last. For self-review/tests."""
    try:
        d = _audit_dir()
        if not d.is_dir():
            return []
        files = sorted(d.glob("*.jsonl"))
        if not files:
            return []
        lines = files[-1].read_text(encoding="utf-8", errors="ignore").splitlines()
        out = []
        for ln in lines[-limit:]:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return out
    except Exception:  # noqa: BLE001
        return []
