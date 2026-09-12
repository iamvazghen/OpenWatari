"""What "it", "that" and "the second one" refer to (SYSTEMS.md 07.F3).

Reference resolution was assumed, not done: the whole history goes to the model and the model is
left to work out what "the second one" meant. That is usually right and occasionally confidently
wrong, and when it is wrong nothing records which reading was taken — so "open the third one"
opening the wrong file looks like a tool bug rather than a misread pronoun.

This resolves the references that are actually resolvable **deterministically**, and declines the
rest. It is not a coreference model and must not become one; it is the part of the problem with a
right answer:

  * **ordinals into a list Afon just produced** — "the second one", "number 3", "the last one".
    This is where the failure is both most likely and most expensive, because the list is usually
    files, tasks or search results and acting on the wrong element is a real action on the wrong
    thing.
  * **a bare pronoun with exactly one recent concrete candidate** — a filename, a quoted string or
    a proper noun in the last couple of turns.

Everything else returns `None`. **A wrong binding is worse than no binding**: no binding leaves the
model exactly where it is today, while a wrong one actively misleads it, so every rule here is
written to decline rather than reach. Two candidates means ambiguous means decline.

    from afon.brain.references import resolve
    r = resolve("open the second one", history)
    r.phrase, r.antecedent      # 'the second one', 'plan.md'

ponytail: regex and a list index, no parser and no model. The budget is a few hundred microseconds
on the answer path, and the cases above are the ones a parser would get right anyway.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Word ordinals Afon actually hears. 1-indexed as spoken; "the last one" is handled separately
#: because it indexes from the other end and getting that wrong is an off-by-one on a real action.
#: Bare cardinals ("one", "two") are deliberately absent. With a list in history, "give me one
#: more" would otherwise index into it and hand back the first item — a wrong binding produced by
#: a word that was not a reference at all.
_ORDINALS = {
    "first": 1, "1st": 1,
    "second": 2, "2nd": 2,
    "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4,
    "fifth": 5, "5th": 5,
    "sixth": 6, "6th": 6, "seventh": 7, "7th": 7,
    "eighth": 8, "8th": 8, "ninth": 9, "9th": 9, "tenth": 10, "10th": 10,
}

#: "the second one", "the 2nd", "number three", "option 2". The trailing noun is optional because
#: "the second" alone is how people actually speak.
_ORDINAL_RE = re.compile(
    r"\b(?:the\s+)?(?:(?P<word>%s)|(?:number|option|item|no\.?)\s+(?P<digit>\d{1,2}))"
    r"(?:\s+(?:one|option|item|file|task|result))?\b" % "|".join(_ORDINALS),
    re.I,
)

_LAST_RE = re.compile(r"\b(?:the\s+)?last\s+one\b|\bthe\s+last\b(?!\s+\w)", re.I)

#: The bare pronouns worth trying. "them"/"those" are plural and only ever resolve to a whole list,
#: which is a different answer shape, so they are out of scope rather than half-supported.
_PRONOUN_RE = re.compile(r"\b(it|that|this|that\s+one|this\s+one)\b", re.I)

#: "it" is not always referential — "it's raining", "it is late", "make it quick". Binding those to
#: the last filename is exactly the confident wrongness this module exists to avoid.
_NON_REFERENTIAL = re.compile(
    r"\bit(?:'s|\s+is|\s+was|\s+will|\s+seems|\s+looks|\s+feels|\s+takes|\s+depends)\b"
    r"|\bmake\s+it\b|\bit\s+doesn'?t\s+matter\b",
    re.I,
)

#: What counts as a concrete thing a pronoun may point at. Deliberately narrow: a filename, a
#: quoted string, or a capitalised multi-word name. A common noun ("the report") is excluded
#: because the last common noun in a conversation is almost never the referent.
_FILENAME_RE = re.compile(r"\b[\w][\w.-]*\.[a-z]{1,5}\b")
_QUOTED_RE = re.compile(r"[\"'“‘]([^\"'”’]{2,60})[\"'”’]")
_PROPER_RE = re.compile(r"\b(?:[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*)\b")

#: Words that look proper because they start a sentence or are shouted, but name nothing.
_NOT_PROPER = {
    "The", "This", "That", "There", "Then", "They", "Them", "What", "When", "Where", "Which",
    "Who", "Why", "How", "And", "But", "For", "Not", "You", "Your", "Yes", "Sir", "Okay", "Right",
    "Sure", "Here", "Now", "Today", "Tomorrow", "Yesterday", "I", "It", "Its", "All", "Any", "Let",
    "Please", "Found", "Done", "Read", "Open", "Tell", "Give", "Show", "Make", "Take",
}

#: How far back a referent may live. Two exchanges: people do not say "it" about something from ten
#: turns ago without naming it again, and reaching further only adds wrong answers.
LOOKBACK = 4


@dataclass(frozen=True)
class Reference:
    """One resolved reference. `source` names the rule, so a wrong binding is diagnosable rather
    than mysterious — which is the whole reason this is deterministic."""

    phrase: str
    antecedent: str
    source: str

    def note(self) -> str:
        """The clarification handed to the model. Phrased as an assumption, not a fact, because it
        is one."""
        return f'(assuming "{self.phrase}" = {self.antecedent})'


def _recent(history: list[dict], roles: tuple[str, ...] = ("assistant", "user")) -> list[str]:
    out = []
    for m in reversed(history[-LOOKBACK:]):
        if m.get("role") in roles and isinstance(m.get("content"), str):
            out.append(m["content"])
    return out


def enumerate_items(text: str) -> list[str]:
    """Pull an ordered list out of a message Afon produced.

    Three shapes, in the order they are trusted. Numbered lines are unambiguous; bulleted lines are
    nearly so; a comma series after a colon is a guess, so it is only accepted when the items look
    like a list (three or more, none of them a sentence) — otherwise "I checked the weather, the
    calendar, and your mail and then did nothing" becomes a three-item list nobody wrote.
    """
    numbered = re.findall(r"^\s*(\d{1,2})[.)]\s+(.+?)\s*$", text, re.M)
    if numbered:
        return [b for _, b in sorted(numbered, key=lambda t: int(t[0]))]

    bulleted = re.findall(r"^\s*[-*•]\s+(.+?)\s*$", text, re.M)
    if bulleted:
        return [b.strip() for b in bulleted]

    # Inline series: "I found three files: notes.md, plan.md and todo.md"
    m = re.search(r":\s*(.+)$", text, re.S)
    if m:
        tail = m.group(1).strip().rstrip(".")
        parts = [p.strip() for p in re.split(r",\s*(?:and\s+)?|\s+and\s+", tail) if p.strip()]
        if len(parts) >= 3 and all(len(p.split()) <= 4 for p in parts):
            return parts
    return []


def _candidates(text: str) -> list[str]:
    """Concrete things in one message, most specific first."""
    out: list[str] = []
    out += _FILENAME_RE.findall(text)
    out += [q.strip() for q in _QUOTED_RE.findall(text)]
    for m in _PROPER_RE.finditer(text):
        p = m.group(0)
        if p.split()[0] in _NOT_PROPER or p in _NOT_PROPER:
            continue
        # A single capitalised word that OPENS a sentence is grammar, not a name: "Opened.",
        # "Calling.", "Saved it." Treating those as referents bound "delete that" to the word
        # "Opened", which is the confidently-wrong answer this module exists to avoid. A
        # multi-word name in the same position is still a name ("Sarah Connor called").
        if " " not in p:
            before = text[:m.start()].rstrip()
            if not before or before[-1] in ".!?\n":
                continue
        out.append(p)
    seen, uniq = set(), []
    for c in out:
        if c.lower() not in seen:
            seen.add(c.lower())
            uniq.append(c)
    return uniq


def resolve(text: str, history: list[dict] | None = None) -> Reference | None:
    """Bind one reference in `text`, or return None. Never raises; never guesses between two."""
    if not text or not isinstance(text, str):
        return None
    history = history or []

    # --- ordinals into the most recent list Afon produced -------------------------------------
    items: list[str] = []
    for msg in _recent(history, ("assistant",)):
        items = enumerate_items(msg)
        if items:
            break

    if items:
        if _LAST_RE.search(text):
            return Reference(_LAST_RE.search(text).group(0).strip(), items[-1], "list:last")
        m = _ORDINAL_RE.search(text)
        if m:
            n = _ORDINALS[m.group("word").lower()] if m.group("word") else int(m.group("digit"))
            # Out of range is a decline, never a clamp. "the fourth one" against three items means
            # the owner and Afon disagree about the list, and silently handing back the third is
            # how the wrong file gets opened.
            if 1 <= n <= len(items):
                return Reference(m.group(0).strip(), items[n - 1], "list:ordinal")
            return None

    # --- a bare pronoun with exactly one recent concrete candidate -----------------------------
    if _NON_REFERENTIAL.search(text):
        return None
    pm = _PRONOUN_RE.search(text)
    if not pm:
        return None
    # A pronoun in the same breath as its own antecedent needs no help.
    if _candidates(text):
        return None
    for msg in _recent(history):
        cands = _candidates(msg)
        if len(cands) == 1:
            return Reference(pm.group(0).strip(), cands[0], "pronoun:unique")
        if len(cands) > 1:
            return None  # ambiguous: decline rather than pick the nearest
    return None


def _selfcheck() -> None:
    """The thirty-second version. `bench/test_reference_resolution.py` is the corpus and the gate."""
    h = [{"role": "assistant", "content": "1. Paris\n2. Lyon\n3. Nice"}]
    assert resolve("tell me about the second one", h).antecedent == "Lyon"
    assert resolve("the last one", h).antecedent == "Nice"
    assert resolve("number 3", h).antecedent == "Nice"
    assert resolve("the fourth one", h) is None, "out of range must decline, not clamp"

    h2 = [{"role": "user", "content": "read notes.md"}, {"role": "assistant", "content": "Done."}]
    assert resolve("summarise it", h2).antecedent == "notes.md"
    assert resolve("it's raining", h2) is None, "non-referential 'it' must not bind"
    assert resolve("open it", []) is None, "no history means no binding"

    h3 = [{"role": "assistant", "content": "I read notes.md and plan.md."}]
    assert resolve("open it", h3) is None, "two candidates is ambiguous — decline"
    print("references: selfcheck passed")


if __name__ == "__main__":
    _selfcheck()
