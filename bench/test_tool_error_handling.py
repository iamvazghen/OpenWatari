"""H2.2 — every tool degrades in Afon's voice, never as a raw exception name.

`agent.py`'s blanket catch stops a bad tool crashing the brain, but what the owner then HEARS is
"That tool hit an error: KeyError". These modules had no handling of their own: documents,
relationship and protocols (9 tools), plus a genuine crash in channels where `int(args["limit"])`
raised ValueError whenever the model filled the field with a word.

The point isn't only politeness. `tool_failed()` recognises the shapes `tool_error`/`not_configured`
produce, and proactive/digest callers use it to avoid reading a failure aloud as if it were data.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f" — {detail}" if detail and not ok else ""))


def _boom(*_a, **_k):
    raise RuntimeError("disk on fire")


def main() -> None:
    import afon.brain.tools.channels as channels
    import afon.brain.tools.documents as docs
    import afon.brain.tools.protocols as protocols
    import afon.brain.tools.relationship as rel
    from afon.brain import docstore
    from afon.brain.relationship import RELATIONSHIP
    from afon.brain.tools.base import tool_failed

    print("[1] relationship tools survive a broken store")
    for fn, args, patch in (
        (rel.note_sensitivity, {"topic": "money"}, "add_sensitivity"),
        (rel.note_running_joke, {"joke": "the kettle"}, "add_running_joke"),
        (rel.relationship_status, {}, "render"),
    ):
        real = getattr(type(RELATIONSHIP), patch)
        setattr(type(RELATIONSHIP), patch, _boom)
        try:
            out = asyncio.run(fn(args))
        finally:
            setattr(type(RELATIONSHIP), patch, real)
        check(f"{fn.__name__} degrades in prose", "RuntimeError" not in out or "couldn't" in out, out[:70])
        check(f"...and {fn.__name__} is detectable as a failure", tool_failed(out), out[:70])

    print("\n[2] document tools survive a broken index")
    docstore.STORE.load_bytes("t.md", b"# T\n\nalpha beta gamma\n")
    real_ask = type(docstore.STORE).ask
    type(docstore.STORE).ask = _boom
    try:
        out = asyncio.run(docs.ask_document({"query": "alpha"}))
    finally:
        type(docstore.STORE).ask = real_ask
    check("ask_document degrades in prose", tool_failed(out), out[:70])

    real_clear = type(docstore.STORE).clear
    type(docstore.STORE).clear = _boom
    try:
        out = asyncio.run(docs.close_document({}))
    finally:
        type(docstore.STORE).clear = real_clear
    check("close_document degrades in prose", tool_failed(out), out[:70])
    docstore.STORE.clear()

    print("\n[3] run_protocol survives an exploding runner")
    real_run = protocols._run

    async def boom_run(*_a, **_k):
        raise RuntimeError("pc link exploded")

    protocols._run = boom_run  # type: ignore[assignment]
    try:
        out = asyncio.run(protocols.run_protocol({"name": "ping", "password": "x"}))
    finally:
        protocols._run = real_run  # type: ignore[assignment]
    check("run_protocol degrades in prose", tool_failed(out), out[:70])
    check("...naming the protocol that failed", "ping" in out, out[:70])

    print("\n[4] channels: a non-numeric limit is a default, not a ValueError")
    # The model fills `limit` from speech; "twelve"/""/None are all realistic and all used to raise.
    for bad in ("twelve", "", None, "3.5", {}):
        got = channels._limit({"limit": bad}, 12, 1, 30)
        check(f"limit={bad!r} -> {got}", got == 12, str(got))
    check("a real number is honoured", channels._limit({"limit": 7}, 12, 1, 30) == 7)
    check("...and clamped to the ceiling", channels._limit({"limit": 999}, 12, 1, 30) == 30)
    check("...and to the floor", channels._limit({"limit": 0}, 20, 5, 30) == 5)

    print("\n[5] channels: an empty result is a complete answer, not an IndexError")

    async def no_videos(_name, _limit):
        return [], None

    real_resolve = channels._resolve_videos
    channels._resolve_videos = no_videos  # type: ignore[assignment]
    try:
        for fn in (channels.list_channel_videos, channels.play_random_from_channel,
                   channels.play_latest_from_channel):
            out = asyncio.run(fn({"channel": "lofi girl"}))
            check(f"{fn.__name__} says it found nothing", "couldn't find" in out or "no videos" in out,
                  out[:70])
    finally:
        channels._resolve_videos = real_resolve  # type: ignore[assignment]

    print("\n[6] contacts: an unexpected save failure is spoken, not raised")
    import afon.brain.contacts as _c
    import afon.brain.tools.contacts as ct
    real_save = type(_c.BOOK).save
    type(_c.BOOK).save = _boom
    try:
        out = asyncio.run(ct.save_contact({"name": "Ada", "email": "a@b.c"}))
    finally:
        type(_c.BOOK).save = real_save
    check("save_contact degrades in prose", tool_failed(out), out[:70])

    print("\n[7] The degradation contract is asserted by a MARKER, not by prose (J1.3)")
    # 61 call sites return not_configured(); ten bench files used to grep the literal sentence
    # "isn't configured yet" for themselves, so rewording it would have left them ALL green with
    # the guarantee gone. These checks compare the PRODUCER against the PREDICATE, so the pair
    # cannot drift: reword the sentence and this fails immediately, in one place.
    from afon.brain.tools.base import is_not_configured, not_configured, tool_error

    _nc = not_configured("Gmail", "an OAuth token")
    check("not_configured() output is recognised by is_not_configured()", is_not_configured(_nc), _nc)
    check("...and counts as a failure for data consumers", tool_failed(_nc), _nc)
    _err = tool_error("calendar read", RuntimeError("boom"))
    check("a real ERROR is a failure but NOT 'not configured'",
          tool_failed(_err) and not is_not_configured(_err), _err)
    check("ordinary data is neither",
          not tool_failed("You have 3 meetings today, sir.")
          and not is_not_configured("You have 3 meetings today, sir."))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
