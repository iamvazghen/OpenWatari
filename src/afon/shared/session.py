"""One conversation identity, stable across reconnects, entry points and devices (07.F1).

The bar S07 sets is that a conversation survives a walk to the kitchen, a reconnect, and a night's
sleep. Most of that already worked — the brain keeps one working history and journals it at a
conversation boundary — but the identity of the conversation was never written down, and the
session ids on the wire were per-entry-point constants:

    brain_client.py   "laptop-1"
    remote_brain.py   "laptop-edge"
    edge_lite.py      "android-1"

So one laptop appeared to the brain as two different sessions depending on which launcher ran, and
nothing tied a session to the conversation it belonged to. That is invisible until you try to
answer "what were we talking about" after a reconnect, or to attribute a journal entry.

A session id is now `<device>:<conversation>`. The device half says which box is speaking; the
conversation half is shared by every device and survives restarts because it is written to the
state directory. Rotating it is an explicit act, performed exactly where the brain already decides
a conversation has ended — `AfonAgent.reset_session` — so the id and the cleared history can never
disagree about where the boundary was.

    from afon.shared.session import device_session_id, conversation_of
    device_session_id("laptop")      # 'laptop:9f2c1a...'
    conversation_of("laptop:9f2c1a") # '9f2c1a'

ponytail: the id is a hex token in a file, not a row in a store. It is one value read at connect
time and written at a boundary; a table would be a schema, a migration and a lock for something
`read_text()` already does correctly.
"""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

from loguru import logger

from afon.shared.paths import state_dir

#: The separator between the device half and the conversation half. A colon rather than a hyphen
#: because device names and the old ids both contain hyphens ("laptop-1"), and a parser that splits
#: on the wrong character silently returns half an id instead of failing.
SEP = ":"

#: Conversation ids are hex so they are safe in a filename, a log line, a URL and a JSON key
#: without escaping. 16 hex chars is 64 bits — far past collision risk for one person's
#: conversations, and short enough to read out in a log.
_ID_CHARS = 16
_ID_RE = re.compile(r"^[0-9a-f]{%d}$" % _ID_CHARS)

#: Anything that is not a known device shape is still accepted, because rejecting an unknown device
#: would mean a new client cannot connect until this file is edited. It is normalised instead.
_DEVICE_RE = re.compile(r"[^a-z0-9_-]+")


def _path() -> Path:
    return state_dir() / "conversation_id"


def _new_id() -> str:
    return secrets.token_hex(_ID_CHARS // 2)


def current() -> str:
    """The conversation in progress, creating one on first use.

    Fail-quiet in the useful direction: if the file cannot be read OR written, a fresh id is
    returned rather than raising. A conversation that cannot be named must still happen — an
    assistant that refuses to talk because it could not write a bookkeeping file would be a much
    worse failure than a conversation that is hard to attribute afterwards.
    """
    p = _path()
    try:
        if p.exists():
            val = p.read_text(encoding="utf-8").strip()
            if _ID_RE.match(val):
                return val
            logger.debug(f"session: ignoring malformed conversation id {val!r}")
    except OSError as e:
        logger.debug(f"session: cannot read conversation id ({type(e).__name__})")
    return rotate("first use")


def rotate(reason: str = "manual") -> str:
    """Start a new conversation and return its id.

    Called where the brain already decides a conversation has ended, so the id and the cleared
    working history move together. Writing is atomic-ish via a temp file and replace, because a
    half-written id read back would look malformed and silently start a third conversation.
    """
    val = _new_id()
    p = _path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(val, encoding="utf-8")
        os.replace(tmp, p)
        logger.info(f"conversation {val} started ({reason})")
    except OSError as e:
        # The id is still returned and still used for this process's lifetime; only its
        # persistence across a restart is lost.
        logger.warning(f"session: conversation id not persisted ({type(e).__name__}): {e}")
    return val


def device_session_id(device: str = "laptop", conversation: str | None = None) -> str:
    """The id a client puts on the wire. Every entry point on one device produces the same value."""
    dev = _DEVICE_RE.sub("-", (device or "laptop").strip().lower()) or "laptop"
    return f"{dev}{SEP}{conversation or current()}"


def conversation_of(session_id: str) -> str | None:
    """The conversation half of a session id, or None for an id that does not carry one.

    None is deliberate and must stay distinguishable from a real id: the old constants
    ("laptop-1") and any third-party client carry no conversation, and inventing one for them
    would attribute their turns to whatever conversation happened to be current.
    """
    if not session_id or SEP not in session_id:
        return None
    _, _, tail = session_id.partition(SEP)
    return tail if _ID_RE.match(tail) else None


def device_of(session_id: str) -> str:
    """The device half, or the whole id when it carries no separator — which is what the legacy
    constants are: a device name and nothing else."""
    if not session_id:
        return "unknown"
    head, sep, _ = session_id.partition(SEP)
    return head if sep else session_id


def _selfcheck() -> None:
    """The thirty-second version. `bench/test_session_identity.py` is the gate."""
    import sys
    import tempfile

    # `sys.modules[__name__]`, NOT `import afon.shared.session` — run as `python -m`, this file is
    # `__main__` and importing it by name would load a SECOND copy, leaving the functions under
    # test pointing at the unpatched path. The first version of this check did exactly that and
    # wrote to the real state directory while asserting against a temp one.
    me = sys.modules[__name__]

    with tempfile.TemporaryDirectory() as d:
        saved = me._path
        me._path = lambda: Path(d) / "conversation_id"  # type: ignore[assignment]
        try:
            a = current()
            assert _ID_RE.match(a), a
            assert current() == a, "a second read must not start a new conversation"

            sid = device_session_id("laptop")
            assert sid == f"laptop{SEP}{a}"
            assert conversation_of(sid) == a and device_of(sid) == "laptop"
            # Two entry points on one device agree — the whole point of the change.
            assert device_session_id("laptop") == device_session_id("LAPTOP")
            # Two devices, one conversation.
            assert conversation_of(device_session_id("iphone")) == a

            b = rotate("selftest")
            assert b != a and current() == b, "rotate must persist"

            # Legacy and foreign ids carry no conversation, and must not be given one.
            for legacy in ("laptop-1", "laptop-edge", "android-1", "", "laptop:zzz"):
                assert conversation_of(legacy) is None, legacy
            assert device_of("laptop-1") == "laptop-1"

            # A corrupt file starts a new conversation instead of propagating nonsense.
            me._path().write_text("not-an-id", encoding="utf-8")
            c = current()
            assert _ID_RE.match(c) and c != b
        finally:
            me._path = saved  # type: ignore[assignment]
    print("session: selfcheck passed")


if __name__ == "__main__":
    _selfcheck()
