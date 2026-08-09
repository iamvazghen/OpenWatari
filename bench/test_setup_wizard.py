"""Setup wizard — the pure, non-interactive bits that turn a clone into someone's own assistant.

Covers: identity keys render into .env, secret values are masked (never shown), and profile/persona
seeding copies a template only when the user's private file is absent (never clobbers).

    uv run python bench/test_setup_wizard.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  XX  {name}  {detail}")


def main() -> None:
    from afon import setup_wizard as w

    # render_env swaps only the keys we collected, preserving every other template line + comment.
    template = (
        "# header comment\n"
        "AFON_ASSISTANT_NAME=Afon\n"
        "AFON_USER_NAME=\n"
        "AFON_USER_ADDRESS=\n"
        "AFON_UNTOUCHED=keepme   # inline comment\n"
    )
    out = w.render_env(template, {
        "AFON_ASSISTANT_NAME": "Aria",
        "AFON_USER_NAME": "Dana",
        "AFON_USER_ADDRESS": "ma'am",
    })
    check("identity name rendered", "AFON_ASSISTANT_NAME=Aria" in out)
    check("identity user rendered", "AFON_USER_NAME=Dana" in out)
    check("identity address rendered", "AFON_USER_ADDRESS=ma'am" in out)
    check("untouched line preserved verbatim", "AFON_UNTOUCHED=keepme   # inline comment" in out)
    check("header comment preserved", "# header comment" in out)

    # mask never reveals a secret in full.
    m = w.mask("supersecrettoken12345")
    check("mask hides the middle of a secret", "supersecrettoken12345" not in m and "..." in m)
    check("blank masks to (blank)", w.mask("") == "(blank)")

    # seed_if_missing copies a template only when the target is absent, and never clobbers.
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "about.example.md"
        dst = Path(d) / "about.md"
        src.write_text("TEMPLATE BODY", encoding="utf-8")
        check("seeds when target missing", w.seed_if_missing(src, dst) is True)
        check("seeded content copied", dst.read_text(encoding="utf-8") == "TEMPLATE BODY")
        dst.write_text("USER EDITED", encoding="utf-8")
        check("does NOT clobber an existing file", w.seed_if_missing(src, dst) is False)
        check("user content preserved", dst.read_text(encoding="utf-8") == "USER EDITED")
        missing = Path(d) / "nope.example.md"
        check("no-op when source missing", w.seed_if_missing(missing, Path(d) / "x.md") is False)

    # Idempotent re-run: parse_env reads an existing .env, and rendering over that .env (not the
    # template) preserves keys/comments the user never revisited.
    existing = (
        "# my hand-written note\n"
        "AFON_ASSISTANT_NAME=Aria\n"
        "AFON_EXTRA_HAND_EDIT=custom-value\n"
        "AFON_PROTOCOL_PING_PASSWORD=oldpass\n"
    )
    cur = w.parse_env(existing)
    check("parse_env reads values", cur["AFON_ASSISTANT_NAME"] == "Aria")
    check("parse_env keeps extras", cur["AFON_EXTRA_HAND_EDIT"] == "custom-value")
    rerun = w.render_env(existing, {"AFON_ASSISTANT_NAME": "Nova",
                                    "AFON_PROTOCOL_PING_PASSWORD": cur["AFON_PROTOCOL_PING_PASSWORD"]})
    check("re-run updates revisited key", "AFON_ASSISTANT_NAME=Nova" in rerun)
    check("re-run keeps hand edits", "AFON_EXTRA_HAND_EDIT=custom-value" in rerun)
    check("re-run keeps hand comments", "# my hand-written note" in rerun)
    check("protocol password NOT rotated", "AFON_PROTOCOL_PING_PASSWORD=oldpass" in rerun)

    # Key probes exist for every provider the wizard collects a probeable secret for.
    for p in ("deepgram", "elevenlabs", "notion", "telegram-bot", "groq", "cerebras", "tavily"):
        check(f"probe wired: {p}", callable(w.PROBES.get(p)))
    check("bad key fails validation (live)", w.PROBES["deepgram"]("not-a-real-key") in (False, None))

    # ask_key's retry loop is the "fail LOUDLY, don't silently accept a wrong key" behavior. Drive it
    # with scripted prompts + a deterministic probe so no network is needed.
    real_ask, real_yes = w.ask, w.yes
    try:
        typed = iter(["wrong-key-1", "good-key"])   # first key bad, second good
        w.ask = lambda q, default=None, secret=False, choices=None: next(typed)
        w.yes = lambda q, default=False: True         # "re-enter?" -> yes
        # Probe: only "good-key" validates. A False means invalid -> ask_key must loop and re-prompt.
        got = w.ask_key("Test key", probe=lambda k: k == "good-key")
        check("ask_key rejects a bad key then accepts the re-entered good one", got == "good-key", got)

        # If the API is unreachable (probe -> None), ask_key must KEEP the typed key, not loop forever.
        typed2 = iter(["some-key"])
        w.ask = lambda q, default=None, secret=False, choices=None: next(typed2)
        got2 = w.ask_key("Test key", probe=lambda k: None)   # None = couldn't reach API
        check("ask_key keeps the key when the API is unreachable (offline-safe)", got2 == "some-key", got2)

        # Enter (empty) on a re-run keeps the existing masked value without probing.
        w.ask = lambda q, default=None, secret=False, choices=None: ""
        got3 = w.ask_key("Test key", probe=lambda k: (_ for _ in ()).throw(AssertionError("probed!")),
                         keep="existing-value")
        check("ask_key keeps existing value on empty input without probing", got3 == "existing-value", got3)
    finally:
        w.ask, w.yes = real_ask, real_yes

    # Scripted END-TO-END run in a sandbox: the full flow writes a correct .env, and a re-run
    # keeps the auth token + protocol passwords (idempotence at the flow level, not just render).
    with tempfile.TemporaryDirectory() as d:
        sandbox = Path(d)
        (sandbox / "personality").mkdir()
        (sandbox / "memory").mkdir()
        real = {k: getattr(w, k) for k in
                ("OUT", "TEMPLATE", "PERSONA", "PERSONA_EXAMPLE", "MEMORY_DIR",
                 "ask", "yes", "ask_key")}
        try:
            w.OUT = sandbox / ".env"
            w.TEMPLATE = Path(w.REPO_ROOT) / ".env.example"
            w.PERSONA = sandbox / "personality" / "afon.md"
            w.PERSONA_EXAMPLE = real["PERSONA_EXAMPLE"]
            w.MEMORY_DIR = sandbox / "memory"

            def scripted_ask(q, default=None, secret=False, choices=None):
                if "Deployment" in q:
                    return "vps"
                if "host or IP" in q:
                    return "100.1.2.3"
                return default or "x"

            w.ask = scripted_ask
            w.yes = lambda q, default=False: ("already exists" in q) or ("proactive" in q.lower())
            w.ask_key = lambda label, probe=None, keep="": keep or "test-key"

            w.run()
            env1 = w.parse_env(w.OUT.read_text(encoding="utf-8"))
            check("e2e: STT provider written", env1["AFON_STT_PROVIDER"] == "deepgram")
            check("e2e: brain URL from host answer",
                  env1["AFON_BRAIN_WS_URL"] == "ws://100.1.2.3:8765/voice")
            check("e2e: auth token generated", len(env1["AFON_API_AUTH_TOKEN"]) > 20)
            check("e2e: protocol password generated",
                  len(env1["AFON_PROTOCOL_PHOENIX_PASSWORD"]) > 8)
            check("e2e: persona seeded", w.PERSONA.exists())

            w.run()  # re-run: token + passwords must survive
            env2 = w.parse_env(w.OUT.read_text(encoding="utf-8"))
            check("e2e re-run: token stable",
                  env2["AFON_API_AUTH_TOKEN"] == env1["AFON_API_AUTH_TOKEN"])
            check("e2e re-run: protocol password stable",
                  env2["AFON_PROTOCOL_PHOENIX_PASSWORD"] == env1["AFON_PROTOCOL_PHOENIX_PASSWORD"])
        finally:
            for k, v in real.items():
                setattr(w, k, v)

    # The shipped templates the wizard seeds from actually exist.
    check("persona.example.md ships", w.PERSONA_EXAMPLE.exists())
    for src, _ in w.PROFILE_SEEDS:
        check(f"{src} ships", (w.MEMORY_DIR / src).exists())

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    raise SystemExit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
