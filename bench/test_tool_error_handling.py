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

    print("\n[6] 03.R2 — the types the schema can actually prove, checked before dispatch")
    from afon.brain.tools import schemas_by_name
    from afon.brain.argcheck import check_args

    [weather] = schemas_by_name(["weather"])
    _, problem = check_args("weather", {"location": "Cologne"}, weather)
    check("a good call is dispatchable", problem == "", problem)

    fake = {"function": {"parameters": {"type": "object", "required": ["n"],
                                        "properties": {"n": {"type": "integer"},
                                                       "on": {"type": "boolean"}}}}}
    out, problem = check_args("t", {"n": "5", "on": "yes"}, fake)
    check("the stringified number a model actually sends is coerced, not bounced",
          problem == "" and out == {"n": 5, "on": True}, f"{out} {problem}")
    _, problem = check_args("t", {"n": "five"}, fake)
    check("...but a word is not a number, and that IS worth a round-trip", "integer" in problem)
    check("...and the rejection names the argument, so it is fixable in one attempt",
          "'n'" in problem or '"n"' in problem, problem)
    _, problem = check_args("t", {"n": True}, fake)
    check("a boolean where a count belongs is caught (True is an int in Python)", bool(problem))
    out, problem = check_args("t", {"n": 1, "spelled_differently": "x"}, fake)
    check("an undeclared key rides along — handlers own their synonyms (see base.missing_arg)",
          problem == "" and out["spelled_differently"] == "x", f"{out} {problem}")
    out, problem = check_args("t", {"n": 1}, None)
    check("no schema means no opinion: the call goes through", problem == "")
    _, problem = check_args("t", ["not", "a", "dict"], fake)
    check("a non-object argument list is rejected rather than crashing the dispatcher",
          bool(problem), problem)

    print("\n[7] 03.R2 — `required` is deliberately NOT enforced here, and this pins why")
    # Eleven tools accept a spelling their own schema marks required, because `missing_arg` takes
    # "the full set of accepted spellings, synonyms included" while the schema advertises one of
    # them to keep the catalogue small. Enforcing `required` from the schema would reject calls that
    # work today. This check fails if anyone "tightens" that later.
    import re as _re
    _tools_src = "\n".join(
        p.read_text(encoding="utf-8")
        for p in (Path(__file__).resolve().parents[1] / "src/afon/brain/tools").glob("*.py"))
    _req = {sch["function"]["name"]: (sch["function"].get("parameters") or {}).get("required") or []
            for sch in schemas_by_name([n["function"]["name"] for n in schemas_by_name([])] or [])}
    synonym_tools = []
    for m in _re.finditer(r"missing_arg\(\s*[\"']([a-z_]+)[\"']\s*,\s*args\s*,(.*?),\s*ask=",
                          _tools_src, _re.S):
        tool, names = m.group(1), _re.findall(r"[\"']([a-z_]+)[\"']", m.group(2))
        found = schemas_by_name([tool])
        req = ((found[0]["function"].get("parameters") or {}).get("required") or []) if found else []
        if req and [n for n in names if n not in req]:
            synonym_tools.append((tool, req[0], [n for n in names if n not in req][:3]))
    check(f"{len(synonym_tools)} tools really do accept a spelling their schema requires away",
          len(synonym_tools) >= 5, str(synonym_tools[:3]))
    for tool, req, alts in synonym_tools[:6]:
        [sch] = schemas_by_name([tool])
        _, problem = check_args(tool, {alts[0]: "something"}, sch)
        check(f"{tool}({alts[0]}=…) is NOT rejected, though the schema requires {req!r}",
              problem == "", problem)

    print("\n[8] 03.R2 — a rejected call becomes ONE repair instruction, not prose")
    from afon.brain import agent as A
    from afon.brain.tools.base import ErrorKind, kind_of, tool_error

    # The handler's own rejection is already TYPED, so nothing parses a sentence to notice it.
    rejected = tool_error("send a push", ValueError("unknown argument(s) ['body_text']"))
    check("a malformed call is typed BAD_ARGS by the handler, not just worded badly",
          kind_of(rejected) is ErrorKind.BAD_ARGS, str(kind_of(rejected)))

    check("the repair tells the model to call again with corrections",
          "again with the arguments corrected" in A._BAD_ARGS_REPAIR)
    check("...and explicitly not to apologise for something that has not happened",
          "nothing has happened yet" in A._BAD_ARGS_REPAIR)
    check("...and not to answer from memory instead, which is the fabrication this invites",
          "from memory" in A._BAD_ARGS_REPAIR)
    check("the SECOND failure stops the loop rather than inviting a third",
          "third time" in A._BAD_ARGS_GIVE_UP)
    check("...and it ends with the owner being told, not with silence",
          "tell the owner" in A._BAD_ARGS_GIVE_UP.lower())
    check("the two are different instructions", A._BAD_ARGS_REPAIR != A._BAD_ARGS_GIVE_UP)

    src = (Path(__file__).resolve().parents[1] / "src/afon/brain/agent.py").read_text(
        encoding="utf-8")
    check("the repair memo is reset at the start of every turn",
          "self._repaired = set()          # 03.R2" in src,
          "the same tool failing on two unrelated turns is two honest mistakes")
    check("the pre-dispatch check runs before the call joins the runnable set",
          src.index("args, problem = self._check_args(name, args)")
          < src.index("runnable.append((idx, name, args))"))
    check("a handler's typed BAD_ARGS is turned into the repair too",
          "kind_of(result) is ErrorKind.BAD_ARGS" in src,
          "the pre-dispatch check cannot see a wrong KEY; the handler can, and does")
    check("a validator that cannot read a schema fails OPEN (never blocks a working tool)",
          "— dispatching" in src)

    print("\n[9] 03.R4 — the six tools no bench case had ever called")
    # 03.R4's rule is that a tool nothing USES must at least be a tool something CALLS, or it is
    # catalogue tokens spent on code no one checks. Six were in neither category. Calling each one
    # with empty arguments is a small test and not a rubber stamp: it is what found
    # `list_recent_actions`, which was written `def` in a registry the agent awaits and had
    # therefore answered "That tool hit an error: TypeError" since the day it shipped.
    import inspect as _inspect
    import os as _os
    import tempfile as _tempfile

    from afon.brain.tools import tool_handlers as _all_handlers
    from afon.brain.tools.base import kind_of as _kind

    _never_called = ["define_macro", "list_macros", "list_recent_actions", "mark_telegram",
                     "telegram_music", "undo_last"]
    _H = _all_handlers()
    with _tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as _td:
        _prev = _os.environ.get("AFON_STATE_DIR")
        _os.environ["AFON_STATE_DIR"] = _td      # never touch the owner's real macros/undo log
        try:
            for _name in _never_called:
                _fn = _H.get(_name)
                check(f"{_name} is registered", _fn is not None)
                if _fn is None:
                    continue
                check(f"{_name} is awaitable (the agent awaits every handler)",
                      _inspect.iscoroutinefunction(_fn),
                      "a sync handler hits asyncio.ensure_future and raises TypeError")
                try:
                    _res = asyncio.run(_fn({}))
                    _ok = True
                except Exception as _e:  # noqa: BLE001
                    _res, _ok = f"{type(_e).__name__}: {_e}", False
                check(f"{_name}({{}}) answers instead of raising", _ok, str(_res)[:120])
                check(f"...in Afon's voice, not a bare exception name",
                      _ok and isinstance(str(_res), str) and str(_res).strip() != "", str(_res)[:80])
                check(f"...and any failure is TYPED, so a caller can decide",
                      not _ok or _kind(_res) is None or hasattr(_kind(_res), "value"),
                      str(_kind(_res)))
        finally:
            if _prev is None:
                _os.environ.pop("AFON_STATE_DIR", None)
            else:
                _os.environ["AFON_STATE_DIR"] = _prev

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
