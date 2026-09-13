"""35.F1/35.F2 — the emergency path: deterministic, ahead of the model, and entirely local.

What existed was system-level: `ragnarok` and `phoenix` recover *Afon*. Nothing recovered the
owner. A person saying "call an ambulance" reached the same machinery as "what's the weather" — a
model round-trip, a tool choice, a network call to a provider — and every one of those is a thing
that can be slow, wrong, or down at the moment it matters. The plan's budget is three seconds to
the first outward action with no LLM on the critical path, and that is not a performance target: it
is the difference between an assistant and a bystander.

So this sits beside `_catastrophic` in the turn path, before `_prepare_turn`, and it is rules the
owner can read. The plan declined a learned classifier and the reason is the right one — a false
positive here phones somebody in the night, and an unexplainable one cannot be argued with. Every
alarm carries the exact phrase that tripped it.

**Conservative means biased to silence.** The default is no alarm. A pattern only fires when the
sentence is a person saying a thing is happening now, and a long list of ordinary speech is guarded
out: figures of speech ("this bug is killing me"), procedure questions ("what do I do if there's a
fire"), narration ("in the film"), and anything in the past. That bias is deliberate and it has a
cost — a real emergency phrased unusually will not trip it. That cost is paid by the manual
phrase, which is why one exists and why it bypasses every guard: **"Afon, emergency"** always
works. An owner who has to remember one thing should have to remember exactly one thing.

**Local, because the network is a thing that fails during emergencies.** The contact list is a
plain file read from disk. Resolving who to reach must not require a provider to be up, a token to
be valid, or a vault to have synced.

The file, `emergency.md` beside `contacts.md` (and gitignored the same way):

    # category: action target, action target, ...   (in the order to try them)
    medical:  call +491700000000 (Kin), push
    fire:     call 112, push
    intruder: push
    default:  push

Nothing is claimed that did not happen: a rung whose transport is not configured on this host is
reported as not reached, by name. A ladder that reached nobody says so in the first sentence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from afon.config import settings

_REPO_ROOT = Path(__file__).resolve().parents[3]

MEDICAL = "medical"
FIRE = "fire"
INTRUDER = "intruder"
ACCIDENT = "accident"
UNKNOWN = "unknown"
DEFAULT = "default"

#: The manual phrase. Deliberately odd enough that it is never said by accident, and deliberately
#: exempt from every guard below — the whole point of a manual trigger is that it cannot be talked
#: out of firing. Keep this list short; one phrase to remember is the design.
MANUAL = ("afon emergency", "afon, emergency", "emergency protocol", "emergency afon")

#: A pattern fires only when it reads as a person reporting something happening NOW. Each one is a
#: whole clause, not a keyword: "ambulance" alone appears in a dozen ordinary sentences.
PATTERNS: tuple[tuple[str, str], ...] = (
    (MEDICAL, r"\bcall (?:an |the )?ambulance\b"),
    (MEDICAL, r"\bi (?:can'?t|cannot) breathe\b"),
    (MEDICAL, r"\b(?:he|she|they|someone)(?:'s| is| are|'re)? not breathing\b"),
    (MEDICAL, r"\bi'?m having a (?:heart attack|stroke|seizure)\b"),
    (MEDICAL, r"\b(?:he|she|they|someone)(?:'s| is| has| have)? (?:just )?(?:collapsed|unconscious)\b"),
    (MEDICAL, r"\bi'?m bleeding (?:badly|heavily|a lot)\b"),
    (MEDICAL, r"\bi think i'?m (?:dying|going to pass out)\b"),
    (FIRE, r"\bthere'?s a fire\b"),
    (FIRE, r"\b(?:the|my) (?:house|flat|apartment|kitchen|building|car) is on fire\b"),
    (FIRE, r"\bcall the fire (?:brigade|department|service)\b"),
    (INTRUDER, r"\bsomeone(?:'s| is) (?:breaking|broken) in\b"),
    (INTRUDER, r"\bthere(?:'s| is) (?:someone|an intruder) in (?:the|my) (?:house|flat|apartment)\b"),
    (INTRUDER, r"\bcall the police\b"),
    (ACCIDENT, r"\bi'?ve been in (?:a|an) (?:car )?(?:accident|crash)\b"),
    (ACCIDENT, r"\bi'?ve (?:crashed|had a crash)\b"),
)

#: Ordinary speech that contains an alarm phrase without being an alarm. Any of these anywhere in
#: the sentence silences the classifier — biased to silence, on purpose.
GUARDS: tuple[str, ...] = (
    # figures of speech
    "killing me", "dying of laughter", "dying to", "felt like", "feels like", "like a heart attack",
    "scared to death", "bored to death",
    # asking about the procedure rather than living it
    "what do i do if", "what should i do if", "what happens if", "what if", "how do i", "how would i",
    "what would you do", "when should i", "do i need to", "should i call", "is it worth calling",
    "remind me to", "what's the number", "whats the number",
    # somebody else's story
    "in the film", "in the movie", "in the show", "on tv", "in the book", "he said", "she said",
    "they said", "the news said", "apparently", "according to",
    # not now
    "yesterday", "last night", "last week", "last month", "last year", "back in", "used to",
    "when i was", "once,",
    # explicitly not real
    "hypothetically", "for example", "pretend", "imagine", "suppose", "a drill", "practice",
    "practise", "rehearse", "joke", "joking", "test the emergency",
)


@dataclass(frozen=True)
class Alarm:
    """What tripped, and what tripped it. The `why` is the phrase, so it can be argued with."""

    category: str
    why: str
    manual: bool = False

    def __bool__(self) -> bool:
        return True


def classify(text: str) -> Alarm | None:
    """Read one utterance. Returns an Alarm only when it is unambiguous. Never raises."""
    low = " " + re.sub(r"\s+", " ", (text or "")).strip().lower() + " "
    if len(low) < 4:
        return None

    for phrase in MANUAL:
        if phrase in low:
            # No guards. A manual trigger that can be reasoned out of firing is not a manual trigger.
            return Alarm(_category_hint(low), f"you said '{phrase}'", manual=True)

    for guard in GUARDS:
        if guard in low:
            return None

    for category, pattern in PATTERNS:
        m = re.search(pattern, low)
        if m:
            return Alarm(category, f"'{m.group(0).strip()}'")
    return None


def _category_hint(low: str) -> str:
    """After a manual trigger, use any category word present — otherwise say it is unknown."""
    for category, pattern in PATTERNS:
        if re.search(pattern, low):
            return category
    for word, category in (("fire", FIRE), ("medical", MEDICAL), ("ambulance", MEDICAL),
                           ("police", INTRUDER), ("intruder", INTRUDER), ("accident", ACCIDENT)):
        if word in low:
            return category
    return UNKNOWN


# --- the local contact list ------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One rung: how to reach someone, and who."""

    action: str
    target: str = ""
    label: str = ""

    def named(self) -> str:
        """How this rung is said out loud, past tense — 'pushed to your phone', 'called Kin'."""
        who = self.label or self.target
        if self.action == "push":
            return f"pushed to {who}" if who else "pushed to your phone"
        verbs = {"call": "called", "signal": "messaged on Signal", "telegram": "messaged on Telegram",
                 "email": "emailed"}
        return f"{verbs.get(self.action, self.action)} {who or 'the owner'}".strip()


ACTIONS = ("push", "call", "signal", "telegram", "email")

_LABEL = re.compile(r"\(([^)]+)\)")


def _path() -> Path:
    configured = getattr(settings, "emergency_contacts_path", "")
    return Path(configured) if configured else _REPO_ROOT / "emergency.md"


def _parse_step(raw: str) -> Step | None:
    raw = raw.strip()
    if not raw:
        return None
    label = ""
    m = _LABEL.search(raw)
    if m:
        label = m.group(1).strip()
        raw = _LABEL.sub("", raw).strip()
    parts = raw.split(None, 1)
    action = parts[0].lower()
    target = parts[1].strip() if len(parts) > 1 else ""
    return Step(action, target, label)


def ladder(category: str = UNKNOWN, path: Path | None = None) -> list[Step]:
    """Who to reach for this category, in order. Falls back to `default:`, then to a push.

    Read from disk every time, not cached: the file is small, this runs once per emergency, and a
    stale ladder held in a process that has been up for three weeks is exactly the failure this
    system cannot have.
    """
    rows = _read(path)
    steps = rows.get(category) or rows.get(DEFAULT) or []
    if steps:
        return steps
    # No file, or nothing usable in it. A push to the owner is better than silence, and the caller
    # is told the list is missing.
    return [Step("push")]


def configured(path: Path | None = None) -> bool:
    """True if the owner has actually written a contact list. Reported, never assumed."""
    return bool(_read(path))


def _read(path: Path | None = None) -> dict[str, list[Step]]:
    p = path or _path()
    try:
        text = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {}
    out: dict[str, list[Step]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        category, _, rest = line.partition(":")
        category = category.strip().lower()
        if not re.fullmatch(r"[a-z]+", category):
            continue
        steps = [s for s in (_parse_step(chunk) for chunk in rest.split(",")) if s]
        # The first rung must be a real action, or this is prose that happens to contain a colon.
        # Without that check every explanatory line in the file became a category, and one of them
        # can shadow `default:`. A LATER rung with an unknown action is kept on purpose, so a typo
        # is reported as "I don't know how to do that" rather than silently dropped.
        if steps and steps[0].action in ACTIONS:
            out[category] = steps
    return out


# --- acting ---------------------------------------------------------------------------------


#: A single rung may not hang the ladder. Short, because the budget is three seconds to the first
#: outward action and a rung that has not answered in this long is not going to.
STEP_TIMEOUT_S = 2.5


async def _do(step: Step, alarm: Alarm, text: str) -> tuple[bool, str]:
    """Run one rung. Returns (reached, why-not). Never raises."""
    import asyncio

    try:
        if step.action == "push":
            from afon.brain.tools import notify

            ok = await asyncio.wait_for(
                notify.push(text, title=f"EMERGENCY — {alarm.category}"), STEP_TIMEOUT_S)
            return bool(ok), "" if ok else "no push topic is configured on this host"
        if step.action == "telegram":
            from afon.brain.tools.telegram import send_telegram

            said = await asyncio.wait_for(
                send_telegram({"to": step.target, "message": text}), STEP_TIMEOUT_S)
            ok = "not configured" not in said.lower() and "couldn't" not in said.lower()
            return ok, "" if ok else "Telegram isn't configured on this host"
        if step.action == "call":
            from afon.brain.tools.phone import place_call

            said = await asyncio.wait_for(
                place_call({"to": step.target, "message": text}), STEP_TIMEOUT_S)
            ok = "not configured" not in said.lower() and "couldn't" not in said.lower()
            return ok, "" if ok else "the call path isn't set up on this host"
        return False, f"I don't know how to '{step.action}'"
    except asyncio.TimeoutError:
        return False, f"{step.action} didn't answer in {STEP_TIMEOUT_S:g}s"
    except Exception as e:  # noqa: BLE001 — a broken rung must never stop the next one
        return False, f"{type(e).__name__}"


async def raise_alarm(alarm: Alarm, text: str = "", path: Path | None = None) -> str:
    """Work the ladder for this alarm and say plainly who was reached and who was not."""
    message = (text or "").strip() or f"Emergency ({alarm.category})."
    steps = ladder(alarm.category, path=path)
    reached: list[str] = []
    missed: list[str] = []
    for step in steps:
        # Guarded HERE as well as inside `_do`. That is deliberate duplication: `_do` catches what
        # a transport throws, and this catches a bug in `_do` itself. One rung raising must never
        # stop the rungs below it, and in this module the rung below is the one that gets help.
        try:
            ok, why = await _do(step, alarm, message)
        except Exception as e:  # noqa: BLE001
            ok, why = False, f"it failed ({type(e).__name__})"
        (reached if ok else missed).append(step.named() + ("" if ok else f" — {why}"))
    logger.warning(f"EMERGENCY {alarm.category} ({alarm.why}): reached={reached} missed={missed}")

    head = f"Emergency, sir. I heard {alarm.why}"
    head += f" and I'm treating it as {alarm.category}." if alarm.category != UNKNOWN else "."
    if reached:
        body = " I've " + _join(reached) + "."
    else:
        body = " I could not reach anyone: " + _join(missed) + "."
    if reached and missed:
        body += " I could not " + _join(missed) + "."
    if not configured(path):
        body += (" There's no emergency contact list on this host, so that was a push to you and "
                 "nothing else — write one into emergency.md.")
    return head + body


def _join(items: list[str]) -> str:
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


async def _ok_stub(step: "Step") -> tuple[bool, str]:
    """Stands in for `_do` in the self-check: the call rung works, everything else does not."""
    return (step.action == "call", "" if step.action == "call" else "stubbed out")


def _selfcheck() -> None:
    """ponytail: the one runnable check — it fires on the real thing and stays quiet on the jokes."""
    import asyncio
    import tempfile

    assert classify("call an ambulance, she's collapsed").category == MEDICAL
    assert classify("Afon, emergency").manual
    assert classify("this bug is killing me") is None
    assert classify("what do I do if there's a fire?") is None
    assert classify("in the film they call an ambulance") is None
    assert classify("what's the weather") is None

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        p = Path(d) / "emergency.md"
        p.write_text("# who\nmedical: call +49170000 (Kin), push\ndefault: push\n",
                     encoding="utf-8")
        steps = ladder(MEDICAL, path=p)
        assert [s.action for s in steps] == ["call", "push"], steps
        assert steps[0].label == "Kin" and steps[0].target == "+49170000", steps[0]
        assert [s.action for s in ladder(FIRE, path=p)] == ["push"]
        # The ladder is exercised against a STUB. A self-check that runs the real one sends a
        # real push to a real phone, which this one did exactly once before the stub went in.
        # Patched in THIS module's globals, not via `import afon.brain.emergency` — running this
        # file as a script makes it `__main__`, so importing it by name binds a SECOND copy and the
        # stub lands on the wrong one. That mistake sent two real pushes to a real phone before it
        # was caught, which is the whole reason this note is here.
        g = globals()
        real_do = g["_do"]
        try:
            g["_do"] = lambda step, alarm, text: _ok_stub(step)
            said = asyncio.run(raise_alarm(Alarm(MEDICAL, "'call an ambulance'"), "help",
                                           path=p))
        finally:
            g["_do"] = real_do
        assert "called Kin" in said, said
        assert "pushed to your phone" in said and "could not" in said, said
    print("emergency self-check ok")


if __name__ == "__main__":
    _selfcheck()
