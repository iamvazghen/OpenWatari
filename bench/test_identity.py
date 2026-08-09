"""Identity layer — the framework personalization: one persona TEMPLATE becomes anyone's assistant.

Proves that name / address / languages all come from config (.env / setup wizard), that the persona
renders correctly for several profiles, and that no unfilled ``{token}`` ever leaks into the prompt.

    uv run python bench/test_identity.py
"""

from __future__ import annotations

import sys
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
    from afon.config import settings
    from afon.brain import context

    saved = {k: getattr(settings, k) for k in
             ("assistant_name", "user_name", "user_address", "understood_languages", "reply_language")}

    def set_identity(**kw):
        for k, v in kw.items():
            setattr(settings, k, v)

    try:
        # 1. Full profile: name + honorific + multilingual.
        set_identity(assistant_name="Aria", user_name="Dana", user_address="ma'am",
                     understood_languages="English, Spanish", reply_language="English")
        tok = context._identity_tokens()
        check("assistant_name token filled", tok["{assistant_name}"] == "Aria")
        check("owner possessive uses the name", tok["{owner_possessive}"] == "Dana's")
        check("address line uses name + honorific", tok["{address_line}"] == 'Address Dana as "ma\'am".')
        check("language line reply-locks", "always reply in English" in tok["{language_line}"]
              and "Spanish" in tok["{language_line}"])

        sample = "I am {assistant_name}, {owner_possessive} assistant. {address_line} {language_line}"
        rendered = context._apply_identity(sample)
        check("template fully rendered (no braces left)", "{" not in rendered, rendered)
        check("rendered names the assistant + owner", "Aria" in rendered and "Dana's" in rendered)

        # 2. Anonymous profile: no name, no honorific -> graceful, neutral phrasing.
        set_identity(assistant_name="Afon", user_name="", user_address="",
                     understood_languages="English", reply_language="English")
        tok = context._identity_tokens()
        check("no name -> owner possessive is 'your'", tok["{owner_possessive}"] == "your")
        check("no honorific -> addresses naturally", "naturally" in tok["{address_line}"].lower())
        check("single language -> simple line", tok["{language_line}"] == "Speak and understand English.")

        # 3. The real persona file renders with NO unfilled tokens leaking into the prompt.
        set_identity(assistant_name="Jeeves", user_name="Bertie", user_address="sir",
                     understood_languages="English, French", reply_language="English")
        prompt = context.build_system_prompt()
        for token in ("{assistant_name}", "{owner_possessive}", "{address_line}", "{language_line}", "{user_ref}"):
            check(f"no leaked token {token}", token not in prompt)
        check("prompt reflects the configured assistant name", "Jeeves" in prompt)
    finally:
        for k, v in saved.items():
            setattr(settings, k, v)

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    raise SystemExit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
