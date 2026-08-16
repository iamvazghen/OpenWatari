"""One person, one node — however their name was spelled or wherever it arrived (24.F1).

Three stores key on a person's name: the contact book, the entity graph, and L1's learned facts.
Each normalised it its own way — the graph lowercased and collapsed whitespace, the contact book
matched exact-then-any-word — so the same human arrived as several entities and nothing joined
them up:

  * **spelling.** A name's Cyrillic original, its transliteration and its short Latin form are
    one person. Lowercasing settles none of the three, and a voice assistant meets all of them:
    STT transliterates, the owner types the short form, and a Russian-language turn writes the
    Cyrillic.
  * **channel.** A name, an email address and a Telegram handle are three ways to say the same
    person. Anything keyed on the string alone treats them as three.

The transliteration table is the load-bearing part and it is deliberately small: it covers the
Cyrillic the owner's six languages actually use, and nothing else. A general Unicode romaniser
would be a dependency and a lot of behaviour nobody here needs.

`canonical` never *guesses*. An earlier version carried an alias table mapping one spelling of a
name onto another so the two would key identically; that was wrong twice over — it hardcoded a
real person's name into a framework repo, and a rename is not something a normaliser gets to
decide. Spelling
variants are merged by `same_entity` at LOOKUP time instead, where being wrong costs a missed
match rather than a permanently mis-keyed node.

Fuzzy matching is `difflib` (stdlib) with a deliberately high threshold. Names are short, and a
loose threshold merges *different* people — a much worse error than failing to merge one person,
because it sends a message to the wrong human.

    from afon.shared.entities import canonical, same_entity
    canonical("Ярослав")               -> "yaroslav"
    same_entity("Yaroslaw", "Ярослав")  -> True   (canonical alone leaves them apart)
"""

from __future__ import annotations

import difflib
import re
import unicodedata

#: Cyrillic -> Latin, covering Russian and Ukrainian. Longest keys first when applied.
_CYR = {
    "щ": "shch", "ш": "sh", "ч": "ch", "ц": "ts", "ю": "yu", "я": "ya", "ж": "zh", "х": "kh",
    "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e", "ё": "e", "є": "ye",
    "з": "z", "и": "i", "і": "i", "ї": "yi", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n",
    "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "ы": "y", "э": "e",
    "ь": "", "ъ": "",
}

#: Dropped before comparison — they are forms of address, not identity.
_TITLES = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "madam", "herr", "frau", "monsieur",
           "madame", "señor", "senor", "señora", "senora"}

_PUNCT = re.compile(r"[^\w\s@.+-]", re.UNICODE)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_HANDLE = re.compile(r"^@[\w.]{2,}$")
_PHONE = re.compile(r"^\+?\d[\d\s().-]{5,}$")

#: How alike two names must be to be called the same person. Chosen from measurements, not feel.
#: Transliteration variants of one name score 0.83-0.94 (Sergey/Sergei 0.833, Yaroslaw/Ярослав
#: 0.875, Dmitriy/Dmitri 0.923); different people score 0.64-0.80 (Anna/Anne 0.750, Marc/Mark
#: 0.750, John Smith/Jane Smith 0.800). 0.82 sits in the gap.
#:
#: The gap is narrow, which is why `same_entity` does not rely on it alone for multi-word names —
#: a shared surname drags two different people up to 0.80 on string similarity, and one more
#: character of coincidence would have merged them. See the word-wise rule below.
SIMILAR_ENOUGH = 0.82


def is_handle(token: str) -> bool:
    """True if this identifies a person by CHANNEL rather than by name."""
    t = (token or "").strip()
    return bool(_EMAIL.match(t) or _HANDLE.match(t) or _PHONE.match(t))


def canonical(name: str) -> str:
    """The key a person is stored under, whatever they were called at the door.

    Handles are lowercased and otherwise left alone — an email address is already canonical, and
    stripping punctuation from one would destroy it.
    """
    raw = (name or "").strip()
    if not raw:
        return ""
    if is_handle(raw):
        return raw.lower().lstrip("@") if not _EMAIL.match(raw) else raw.lower()

    text = unicodedata.normalize("NFD", raw.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))   # josé -> jose
    out = []
    for ch in text:
        out.append(_CYR.get(ch, ch))
    text = _PUNCT.sub(" ", "".join(out))
    # Edge punctuation only, so an initial ("K.") folds onto the bare letter while a hyphenated
    # name ("Anne-Marie") keeps the hyphen that is part of it.
    words = [w.strip(".+-") for w in text.split()]
    words = [w for w in words if w and w not in _TITLES]
    return " ".join(words)


def same_entity(a: str, b: str) -> bool:
    """Are these two references to one person?"""
    ca, cb = canonical(a), canonical(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    if is_handle(ca) or is_handle(cb):
        return False        # two different handles are two different addresses, never fuzzy-matched
    wa, wb = ca.split(), cb.split()
    if len(wa) > 1 and len(wa) == len(wb):
        # Word-wise for full names. A shared surname pulls two different people up the whole-string
        # score on its own — "john smith" and "jane smith" reach 0.800 purely on " smith" — so the
        # given name has to stand on its own merits. Measured: john/jane is 0.500, which no
        # threshold anywhere near the transliteration band would accept.
        return all(difflib.SequenceMatcher(None, x, y).ratio() >= SIMILAR_ENOUGH
                   for x, y in zip(wa, wb))
    return difflib.SequenceMatcher(None, ca, cb).ratio() >= SIMILAR_ENOUGH


def best_match(query: str, candidates: list[str]) -> str | None:
    """The one candidate that is the same entity as `query`, or None if it is ambiguous."""
    hits = [c for c in candidates if same_entity(query, c)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        return None
    exact = [c for c in hits if canonical(c) == canonical(query)]
    return exact[0] if len(exact) == 1 else None


#: What is worth knowing about an entity, per kind. `unknowns()` reports which of these the stores
#: hold nothing for — 24.F2's "state what it does NOT know". Declared rather than inferred from
#: whatever happens to be stored, because a gap you can only see by knowing what to look for is a
#: gap nobody sees.
FACETS: dict[str, tuple[str, ...]] = {
    "person": ("email", "phone", "telegram", "role", "lives in", "birthday", "related to"),
    "project": ("status", "deadline", "owner", "next step", "location"),
    "place": ("country", "visited", "related to"),
}
DEFAULT_KIND = "person"


def unknowns(known_predicates: list[str], kind: str = DEFAULT_KIND) -> list[str]:
    """Which declared facets have nothing recorded against them."""
    have = {canonical(p) for p in (known_predicates or [])}
    return [f for f in FACETS.get(kind, FACETS[DEFAULT_KIND])
            if not any(canonical(f) in h or h in canonical(f) for h in have if h)]


def _selfcheck() -> None:
    """python -m afon.shared.entities"""
    assert canonical("Ярослав") == "yaroslav", canonical("Ярослав")
    # NOT equal: transliteration is deterministic, and "gh" vs "g" is a spelling difference no
    # rule can settle. `same_entity` merges them; `canonical` does not invent a rename.
    assert canonical("Yaroslaw") == "yaroslaw"
    assert canonical("  Mr.  José   García ") == "jose garcia", canonical("  Mr.  José   García ")
    assert canonical("A@B.COM") == "a@b.com"
    assert same_entity("Yaroslaw", "Ярослав")
    assert same_entity("Freiburg", "freiburg")
    assert not same_entity("John Smith", "Jane Smith"), "different people must not merge"
    assert not same_entity("a@b.com", "c@d.com")
    assert unknowns(["email", "role"])[:1] == ["phone"], unknowns(["email", "role"])
    print("entities selfcheck ok")


if __name__ == "__main__":
    _selfcheck()
