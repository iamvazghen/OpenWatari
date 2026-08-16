"""Afon hears six languages and answers in the one he was spoken to in.

English, German, French, Spanish, Russian, Ukrainian. Three separate things have to hold, and
each fails in its own quiet way:

  1. **Detection.** The hard pair is Russian/Ukrainian. Four letters (і ї є ґ / ы ъ э ё) settle
     most of it, but plenty of ordinary Ukrainian sentences contain none of them, and those are
     precisely the ones that would otherwise be answered in Russian.
  2. **Stickiness.** Voice turns are short. "ok", "да", "genau" carry almost no signal, and a
     fresh guess per turn flips the conversation mid-exchange — worse than never switching,
     because it reads as a malfunction rather than a setting.
  3. **Honesty about the ear.** Deepgram's nova-3 `multi` covers five of the six and NOT
     Ukrainian, and it does not error on Ukrainian — it returns plausible Russian. The transcript
     looks fine, the detector agrees with it, and the reply is confidently in the wrong language.
     A config that lists six while the ear hears five must say so out loud.

Run:
    uv run python bench/test_languages.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.shared import language as L  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


#: Phrases an owner actually says to a voice assistant, not textbook sentences. Several carry no
#: language-unique letters at all, which is the case that matters.
CORPUS: list[tuple[str, str]] = [
    ("what time is it", "en"), ("remind me to call mum tomorrow", "en"),
    ("play some music please", "en"), ("how is the weather in Berlin", "en"),
    ("wie spät ist es", "de"), ("erinnere mich morgen an den Termin", "de"),
    ("kannst du bitte die Musik anmachen", "de"), ("was ist mit dem Wetter", "de"),
    ("quelle heure est-il", "fr"), ("rappelle-moi d'appeler ma mère demain", "fr"),
    ("peux-tu mettre de la musique", "fr"), ("il fait quel temps à Berlin", "fr"),
    ("qué hora es", "es"), ("recuérdame llamar a mi madre mañana", "es"),
    ("puedes poner música por favor", "es"), ("cómo está el tiempo", "es"),
    ("который час", "ru"), ("напомни мне позвонить маме завтра", "ru"),
    ("включи музыку пожалуйста", "ru"), ("какая сегодня погода", "ru"),
    ("котра зараз година", "uk"), ("нагадай мені зателефонувати мамі завтра", "uk"),
    ("увімкни музику будь ласка", "uk"), ("яка зараз погода", "uk"),
]

#: Turns that genuinely carry no language at all. The right answer is "I don't know", not a guess.
NO_SIGNAL = ["ok", "hm", "42", "...", "мм"]

#: Single words that ARE a language, and must still not steer the conversation. Written as its own
#: list after the first version of this file lumped them in with NO_SIGNAL and called them a
#: detection bug — they are not: "да" really is Russian. The defect they expose is different and
#: worse, and section [6] is where it lives: answering "no" to a German question must not switch
#: Afon into English.
ONE_WORD_ANSWERS = [("да", "ru"), ("ja", "de"), ("no", "en"), ("oui", "fr"), ("sí", "es")]


def main() -> int:
    print(f"[1] all six languages are configured options ({len(L.SUPPORTED)})")
    for code in ("en", "de", "fr", "es", "ru", "uk"):
        check(f"{code} — {L.name_of(code)} ({L.SUPPORTED.get(code, ('', ''))[1]})",
              code in L.SUPPORTED)

    print("\n[2] a language is recognised however it is spelled in .env")
    check("English names", L.parse_languages("English, German, Ukrainian") == ["en", "de", "uk"])
    check("endonyms", L.parse_languages("Русский, Deutsch, Español") == ["ru", "de", "es"])
    check("codes", L.parse_languages("en,ru,uk") == ["en", "ru", "uk"])
    check("mixed spellings and stray whitespace",
          L.parse_languages(" english ;Français, ES ") == ["en", "fr", "es"])
    check("an unsupported language is dropped, not guessed at",
          L.parse_languages("English, Klingon, Armenian") == ["en"],
          "silently mapping an unknown name onto a supported one is worse than ignoring it")
    check("order is preserved — the first is the default",
          L.parse_languages("Russian, English")[0] == "ru")

    print(f"\n[3] detection over {len(CORPUS)} spoken phrases")
    wrong = [(t, want, L.detect(t)) for t, want in CORPUS if L.detect(t)[0] != want]
    check(f"every phrase is identified correctly ({len(CORPUS) - len(wrong)}/{len(CORPUS)})",
          not wrong, "\n        " + "\n        ".join(f"{t!r} wanted {w}, got {g}" for t, w, g in wrong))
    by_lang = {}
    for text, want in CORPUS:
        by_lang.setdefault(want, []).append(L.detect(text)[1])
    for code, confs in sorted(by_lang.items()):
        check(f"{L.name_of(code)} is identified confidently (min {min(confs):.2f})",
              min(confs) >= L.CONFIDENT, str(confs))

    print("\n[4] Russian vs Ukrainian — the pair that actually collides")
    check("a Ukrainian-only letter decides it", L.detect("що ти робиш")[0] == "uk")
    check("a Russian-only letter decides it", L.detect("что ты делаешь")[0] == "ru")
    # The case the four-letter test cannot see: shared alphabet throughout.
    check("shared-alphabet Ukrainian is still Ukrainian",
          L.detect("котра зараз година")[0] == "uk",
          "no і/ї/є/ґ in this sentence — without a word-level tie-break it reads as Russian")
    check("shared-alphabet Russian is still Russian",
          L.detect("сколько сейчас время")[0] == "ru")
    check("a bare Cyrillic word defaults to Russian rather than a coin toss",
          L.detect("музыка")[0] == "ru",
          "Russian is both more common here and the alphabetical subset")

    print("\n[5] an utterance with no signal returns none, rather than guessing")
    for text in NO_SIGNAL:
        code, conf = L.detect(text)
        check(f"{text!r} is not claimed confidently", not code or conf < L.CONFIDENT,
              f"claimed {code} at {conf}")
    for text, want in ONE_WORD_ANSWERS:
        code, conf = L.detect(text)
        check(f"{text!r} is read as {L.name_of(want)}, but weakly ({conf})",
              code == want and conf < 0.8, f"got {code} at {conf}")

    print("\n[6] the conversation is sticky — one vague turn does not flip it")
    t = L.LanguageTracker(allowed=list(L.SUPPORTED))
    check("it starts in the default", t.current == "en")
    check("a clear Russian turn switches it", t.observe("напомни мне позвонить маме") == "ru")
    check("'да' keeps Russian", t.observe("да") == "ru")
    check("'ok' keeps Russian too", t.observe("ok") == "ru",
          "a word that exists in every language is not evidence of any of them")
    check("a clear German turn switches again", t.observe("wie spät ist es") == "de")
    check("...and stays there across a vague turn", t.observe("hm") == "de")
    # The one that matters, and the one the first draft of this file missed by calling these words
    # a detection bug instead of a steering one: every short answer below is a REAL word of some
    # other language, so full-confidence detection plus a naive tracker flips the conversation on
    # a single syllable.
    for word, is_really in ONE_WORD_ANSWERS:
        t2 = L.LanguageTracker(allowed=list(L.SUPPORTED))
        t2.observe("wie spät ist es in Berlin")
        check(f"answering {word!r} to a German question stays German (not {is_really})",
              t2.observe(word) == "de", f"flipped to {t2.current}")
    check("but a full sentence in another language does switch",
          t.observe("напомни мне позвонить маме завтра") == "ru")
    check("an unconfigured language does not hijack the conversation",
          L.LanguageTracker(allowed=["en", "de"]).observe("напомни мне позвонить маме") == "en",
          "he cannot be answered in a language the deployment does not support")
    t.reset()
    check("a session reset returns to the default", t.current == "en")

    print("\n[7] the ear is honest about what it cannot hear")
    six = ["en", "de", "fr", "es", "ru", "uk"]
    check("Deepgram's multilingual model is reported as missing Ukrainian",
          L.stt_gaps("deepgram", six) == ["uk"], str(L.stt_gaps("deepgram", six)))
    check("...which is the failure that does NOT raise — it returns plausible Russian",
          "uk" not in L.STT_COVERAGE["deepgram"])
    check("Whisper covers all six", L.stt_gaps("whisper", six) == [])
    check("Moonshine is honest about being English-only",
          set(L.stt_gaps("moonshine", six)) == {"de", "fr", "es", "ru", "uk"})
    check("an unknown provider is not accused of gaps it may not have",
          L.stt_gaps("some-new-provider", six) == [])

    print("\n[8] the prompt tells the model which language to answer in")
    from afon.config import settings

    saved = (settings.reply_language, settings.understood_languages)
    try:
        from afon.brain import context as C

        settings.understood_languages = "English, German, French, Spanish, Russian, Ukrainian"
        settings.reply_language = L.MATCH
        line = C._identity_tokens()["{language_line}"]
        check("mirror mode instructs the model to answer in the owner's language",
              "same language" in line.lower(), line[:100])
        check("...and tells it the language is stated per turn, not to be inferred",
              "each turn" in line.lower(), line[:160])
        # The always-on prompt has a 2000-token budget (test_finetune.py) and this line is in
        # every turn. The first draft cost 27 tokens over it; enumerating the languages here as
        # well would then shrink the headroom again on each language the owner adds.
        check("mirror mode does not enumerate the languages in the always-on prompt",
              "Ukrainian" not in line and len(line) < 200,
              f"{len(line)} chars — the per-turn note names the language, so this need not")
        settings.reply_language = "English"
        line = C._identity_tokens()["{language_line}"]
        check("a pinned reply language still forbids switching",
              "always reply" in line.lower() and "never switch" in line.lower(), line[:100])
    finally:
        settings.reply_language, settings.understood_languages = saved

    print("\n[9] the live turn carries the language, and keeps it")
    import asyncio

    from afon.brain.agent import AfonAgent

    saved = (settings.reply_language, settings.understood_languages)
    try:
        settings.understood_languages = "English, German, French, Spanish, Russian, Ukrainian"
        settings.reply_language = L.MATCH
        agent = AfonAgent()
        agent._self_improve = False
        note = agent._language_note("напомни мне позвонить маме завтра")
        check("a Russian turn is labelled Russian for the model",
              note and "Russian" in note, str(note))
        check("...in the language's own name too, so the model has no room to translate the label",
              note and "Русский" in note, str(note))
        check("a vague follow-up keeps the same language",
              "Russian" in (agent._language_note("ок") or ""), str(agent._language_note("ок")))
        check("a German turn relabels it",
              "German" in (agent._language_note("wie spät ist es") or ""))

        from afon.brain import turn_trace as T
        with T.turn("wie spät ist es") as tr:
            agent._language_note("wie spät ist es")
            captured = tr.language
        check("the turn trace records which language was spoken", captured == "de", captured)

        # The note existing is not the same as the turn carrying it. Checking the helper alone
        # passed happily with the call site in `_prepare_turn` deleted — the same shape of hole as
        # testing `_deprioritise` without `_candidate_chain`. So drive a real turn and read what
        # the model was actually sent.
        class _Capture:
            def __init__(self) -> None:
                self.messages: list[dict] = []

            async def complete(self, messages, tools=None, tool_choice="auto", **kw):
                self.messages = list(messages)

                class _M:
                    tool_calls = None
                    content = "Хорошо, сэр."
                return _M()

        cap = _Capture()
        agent3 = AfonAgent()
        agent3._self_improve = False
        agent3._llm = cap
        asyncio.run(agent3.respond("напомни мне позвонить маме завтра"))
        systems = " ".join(m.get("content") or "" for m in cap.messages if m.get("role") == "system")
        # Match the per-turn note's own words, not the bare language name: the persona line in
        # mirror mode already lists all six, so "Russian in systems" was true with the wiring
        # deleted. A check that passes on the wrong sentence is not a check.
        check("a REAL turn carries the language instruction to the model",
              "The owner is speaking Russian" in systems and "Reply in Russian" in systems,
              systems[-200:] or "<no system messages>")

        settings.reply_language = "English"
        agent2 = AfonAgent()
        agent2._self_improve = False
        check("a single-language deployment pays nothing for any of this",
              agent2._language_note("напомни мне позвонить маме") is None,
              "mirror mode is opt-in; a pinned reply language must not carry a per-turn note")
        cap2 = _Capture()
        agent2._llm = cap2
        asyncio.run(agent2.respond("напомни мне позвонить маме завтра"))
        systems2 = " ".join(m.get("content") or "" for m in cap2.messages if m.get("role") == "system")
        check("...and no language note reaches the model on a pinned deployment",
              "Reply in Russian" not in systems2)
    finally:
        settings.reply_language, settings.understood_languages = saved

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
