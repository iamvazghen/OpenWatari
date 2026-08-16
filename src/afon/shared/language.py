"""Which language is the owner speaking, and which should Afon answer in.

He speaks six: English, German, French, Spanish, Russian and Ukrainian. Until now the prompt said
"understand any of them, but **always reply in English**", which was the honest setting while the
voice was English-only. With a multilingual ElevenLabs voice the interesting mode becomes *answer
in the language he used* — and that needs the language named per turn, not left to the model.

Left to itself a model matches the language of the last utterance, which fails exactly where a
conversation lives: "ok", "да", "genau" carry almost no signal, and one ambiguous turn flips the
whole conversation into the wrong language. So detection is explicit, and **sticky**: a turn that
is not clearly in a new language stays in the current one. Hysteresis, not a fresh guess per turn.

No new dependency. `langdetect`/`fasttext` would be more accurate over a paragraph and are worse
here: they need a model file, they are slow to import on the turn path, and both are *less*
reliable than a script check on the two-to-five word utterances that make up most voice turns.
The hard part of these six is not statistics, it is Russian vs Ukrainian — and that is decided by
four letters, exactly.

    from afon.shared.language import detect, LanguageTracker
    detect("що ти робиш")        -> ("uk", 0.9)
    LanguageTracker().observe("ok")   # keeps whatever was already being spoken
"""

from __future__ import annotations

import re
import unicodedata

#: code -> (English name, endonym). The order is the tie-break order, most-likely first.
SUPPORTED: dict[str, tuple[str, str]] = {
    "en": ("English", "English"),
    "ru": ("Russian", "Русский"),
    "de": ("German", "Deutsch"),
    "fr": ("French", "Français"),
    "es": ("Spanish", "Español"),
    "uk": ("Ukrainian", "Українська"),
}

#: Reply-language sentinel: answer in whichever supported language the owner just used.
MATCH = "match"

# Russian vs Ukrainian is settled by alphabet, not by vocabulary. These letters exist in exactly
# one of the two, so a single occurrence is decisive where a whole stopword table is not.
_UK_ONLY = set("іїєґІЇЄҐ")
_RU_ONLY = set("ыъэёЫЪЭЁ")

_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

# Function words: the highest-frequency, lowest-ambiguity words of each language. Deliberately
# short — a long list adds words that collide across languages ("son" is French and Spanish, "die"
# is German and English) and makes the score worse, not better.
_STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset("the and or but is are was were do does did you i my me we they it "
                    "what when where why how can could would should will not don't isn't to of "
                    "for with about please thanks yes no".split()),
    "de": frozenset("der die das ein eine und oder aber ist sind war waren ich du wir sie mich "
                    "mir mein nicht kein was wann wo warum wie kann könnte soll wird zu von für "
                    "mit über bitte danke ja nein auch noch schon sehr den dem des am im an vom "
                    "beim einen einem morgen heute termin".split()),
    "fr": frozenset("le la les un une et ou mais est sont était étaient je tu nous vous ils "
                    "elles mon ma mes ne pas plus quoi quand où pourquoi comment peux peut "
                    "pourrait doit va de du des pour avec sur s'il merci oui non très".split()),
    "es": frozenset("el la los las un una y o pero es son era eran yo tú usted nosotros ellos "
                    "mi mis no qué cuándo dónde por qué cómo puedo puede podría debe va de del "
                    "para con sobre por favor gracias sí muy también".split()),
    "ru": frozenset("и или но не нет да это что как где когда почему я ты мы вы он она они мой "
                    "моя мне меня тебя нас вас на в с по для из у же бы ли уже очень спасибо "
                    "пожалуйста можешь можно надо".split()),
    "uk": frozenset("і та або але не ні так це що як де коли чому я ти ми ви він вона вони мій "
                    "моя мене тебе нас вас на в з по для із у же би чи вже дуже дякую будь "
                    "ласка можеш можна треба".split()),
}

#: Everyday words that exist in one of Russian/Ukrainian and not the other. The four-letter
#: alphabet test settles most Ukrainian text, but plenty of ordinary sentences happen to contain
#: none of them — "котра зараз година" is entirely shared-alphabet Ukrainian — and those are the
#: sentences that would otherwise be answered in Russian. Chosen for being unmistakable rather
#: than frequent: every one of these has a different, equally common word in the other language.
_RU_UK_MARKERS: dict[str, frozenset[str]] = {
    "uk": frozenset("зараз година годину годин гаразд дякую ласка потрібно треба зробити робиш "
                    "робити розкажи скажи-но чому-то хвилин хвилина завтра-вранці розумію "
                    "перепрошую вибач добраніч привіт-усім слухаю питання їсти пити".split()),
    "ru": frozenset("сейчас спасибо пожалуйста хорошо нужно сделать расскажи который время "
                    "минут минута понимаю извини спокойной привет слушаю вопрос кушать "
                    "сегодня завтра-утром ладно конечно".split()),
}

#: Letters that only ever appear in one of the Latin four. Weaker evidence than a stopword (they
#: survive in loanwords) but decisive on a short utterance that has no function words at all.
_LATIN_HINTS: dict[str, str] = {
    "de": "äöüßÄÖÜ",
    "es": "ñ¿¡ÑáíóúÁÍÓÚ",
    "fr": "àâçèêëîïôûùÀÂÇÈÊËÎÏÔÛÙœ",
}

#: Below this, a turn is "not clearly in any language" and the tracker keeps the current one.
CONFIDENT = 0.5

#: How many words a turn needs before it may CHANGE the conversation's language. Nobody switches
#: language with a single word mid-conversation, but plenty of single words are valid in several
#: of the six — "no" (en/es), "ja" (de), "si" (es/fr), "ok" (all of them). Without this, answering
#: "no" to a German question flips Afon into English, and the owner sees a malfunction rather than
#: a setting. A short utterance is still *detected*; it just does not get to steer.
MIN_SWITCH_WORDS = 3


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text or "")]


def detect(text: str) -> tuple[str, float]:
    """Best guess at the language of `text`, as ``(code, confidence)``.

    Returns ``("", 0.0)`` when there is genuinely nothing to go on — a bare "ok", an emoji, a
    number. That is a real answer, not a failure: the caller is expected to keep whatever language
    the conversation was already in rather than reset it on the flimsiest turn of the exchange.
    """
    text = (text or "").strip()
    if not text:
        return "", 0.0
    words = _words(text)
    if not words:
        return "", 0.0

    if _CYRILLIC.search(text):
        chars = set(text)
        uk_hits, ru_hits = len(chars & _UK_ONLY), len(chars & _RU_ONLY)
        if uk_hits and not ru_hits:
            return "uk", 0.95
        if ru_hits and not uk_hits:
            return "ru", 0.95
        # No decisive letter: weigh the words that differ between the two, then the plain function
        # words, then fall back to Russian — the more common of the two here, and the one whose
        # alphabet is a subset, so an unmarked Cyrillic string is more likely Russian.
        uk_marks = sum(1 for w in words if w in _RU_UK_MARKERS["uk"])
        ru_marks = sum(1 for w in words if w in _RU_UK_MARKERS["ru"])
        if uk_marks != ru_marks:
            winner = "uk" if uk_marks > ru_marks else "ru"
            return winner, min(0.9, 0.75 + 0.05 * abs(uk_marks - ru_marks))
        uk_score = sum(1 for w in words if w in _STOPWORDS["uk"])
        ru_score = sum(1 for w in words if w in _STOPWORDS["ru"])
        if uk_score > ru_score:
            return "uk", 0.6 + min(0.3, 0.1 * uk_score)
        if ru_score > uk_score:
            return "ru", 0.6 + min(0.3, 0.1 * ru_score)
        return "ru", 0.55 if len(words) > 1 else 0.45

    scores: dict[str, float] = {}
    for code in ("en", "de", "fr", "es"):
        hits = sum(1 for w in words if w in _STOPWORDS[code])
        scores[code] = hits / len(words)
    # Accented letters break the ties that function words cannot: "schön", "français", "mañana".
    lowered = text.lower()
    for code, hint_chars in _LATIN_HINTS.items():
        if any(ch in lowered for ch in hint_chars.lower()):
            scores[code] = scores.get(code, 0.0) + 0.5

    best = max(scores, key=lambda c: (scores[c], -list(SUPPORTED).index(c)))
    top = scores[best]
    if top <= 0.0:
        return "", 0.0
    runner = max((v for c, v in scores.items() if c != best), default=0.0)
    # A margin, not a raw score: "the the the" scores 1.0 for English and means little, while one
    # unambiguous function word in a five-word sentence means a lot.
    confidence = min(0.95, 0.45 + top * 0.5 + (top - runner) * 0.4)
    if len(words) < 2:
        # A single word is weak evidence even when it is a perfect match. "no" is English and
        # Spanish; "ja" is German; "si" is Spanish and French. Scoring them at full confidence is
        # how a one-word answer mid-conversation would look like a language change.
        confidence *= 0.6
    return best, round(confidence, 2)


#: What each STT provider can actually transcribe, of the six. Written down because the gap is
#: real and silent: Deepgram nova-3's `multi` code-switching model covers English, Spanish,
#: French, German and Russian but **not Ukrainian**, so a Ukrainian sentence does not fail — it
#: comes back as plausible Russian, and the reply is confidently in the wrong language. A setting
#: that lists six languages while the ear hears five is exactly the kind of thing that looks
#: configured and is not.
STT_COVERAGE: dict[str, frozenset[str]] = {
    "deepgram": frozenset({"en", "es", "fr", "de", "ru"}),   # nova-3, language="multi"
    "whisper": frozenset(SUPPORTED),                          # auto-detect covers all six
    "moonshine": frozenset({"en"}),                           # English-only by design
}


def stt_gaps(provider: str, wanted: list[str] | None = None) -> list[str]:
    """Which of the wanted languages this STT provider cannot hear. Empty is the good answer."""
    covered = STT_COVERAGE.get((provider or "").strip().lower())
    if covered is None:
        return []          # an unknown provider gets the benefit of the doubt, not a false alarm
    return [c for c in (wanted or list(SUPPORTED)) if c in SUPPORTED and c not in covered]


def name_of(code: str) -> str:
    return SUPPORTED.get(code, ("", ""))[0]


def parse_languages(spec: str) -> list[str]:
    """Turn a config string ("English, Russian" / "en,ru") into codes, keeping the given order.

    Accepts names, endonyms and codes because all three end up in a `.env` sooner or later, and a
    setting that silently ignores two of the three spellings is a setting that silently disables
    a language the owner believes he enabled.
    """
    out: list[str] = []
    for raw in (spec or "").replace(";", ",").split(","):
        token = _fold(raw)
        if not token:
            continue
        for code, (english, native) in SUPPORTED.items():
            if token in (code, _fold(english), _fold(native)):
                if code not in out:
                    out.append(code)
                break
    return out


def _fold(text: str) -> str:
    """Casefold and strip accents, so "Français" and "francais" are the same token."""
    stripped = "".join(c for c in unicodedata.normalize("NFD", (text or "").strip())
                       if not unicodedata.combining(c))
    return stripped.casefold()


class LanguageTracker:
    """The language the conversation is currently in.

    Sticky on purpose. Voice turns are short, and a fresh per-turn guess would flip the reply
    language on "ok" or "да" — which is worse than never switching at all, because it happens
    mid-conversation and looks like a malfunction rather than a setting.
    """

    def __init__(self, default: str = "en", allowed: list[str] | None = None) -> None:
        self.allowed = [c for c in (allowed or list(SUPPORTED)) if c in SUPPORTED] or ["en"]
        self.default = default if default in self.allowed else self.allowed[0]
        self.current = self.default
        self.last_detected = ""
        self.last_confidence = 0.0

    def observe(self, text: str) -> str:
        """Feed one utterance; return the language Afon should answer in."""
        code, confidence = detect(text)
        self.last_detected, self.last_confidence = code, confidence
        enough_words = len(_words(text)) >= MIN_SWITCH_WORDS
        if code and confidence >= CONFIDENT and code in self.allowed and enough_words:
            self.current = code
        return self.current

    def reset(self) -> None:
        self.current = self.default
        self.last_detected, self.last_confidence = "", 0.0


def _selfcheck() -> None:
    """python -m afon.shared.language"""
    cases = [
        ("what time is it", "en"), ("please remind me tomorrow", "en"),
        ("wie spät ist es", "de"), ("kannst du mir bitte helfen", "de"),
        ("quelle heure est-il", "fr"), ("peux-tu me rappeler demain", "fr"),
        ("qué hora es", "es"), ("puedes recordarme mañana", "es"),
        ("который час сейчас", "ru"), ("напомни мне завтра пожалуйста", "ru"),
        ("котра зараз година", "uk"), ("нагадай мені завтра будь ласка", "uk"),
    ]
    wrong = [(t, want, detect(t)) for t, want in cases if detect(t)[0] != want]
    assert not wrong, wrong

    t = LanguageTracker(allowed=list(SUPPORTED))
    assert t.observe("который сейчас час") == "ru"
    assert t.observe("да") == "ru", "an ambiguous turn must not reset the language"
    assert t.observe("ok") == "ru", "nor must a word that exists in every language"
    assert t.observe("what time is it in Berlin") == "en"
    assert parse_languages("English, Русский, fr") == ["en", "ru", "fr"]
    print(f"language selfcheck ok ({len(cases)} phrases, {len(SUPPORTED)} languages)")


if __name__ == "__main__":
    _selfcheck()
