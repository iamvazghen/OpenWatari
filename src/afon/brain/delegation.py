"""39.F2/F3 — a delegation is a tracked unit with a deadline, and its answer is checked.

Two things were wrong, and the second is the worse one.

**Nothing was tracked.** The blocking path awaited a string and returned it. The background path
wrapped it in a `TaskQueue` row that `TaskQueue.drop` DELETES the moment the task finishes, so a
successful delegation left no trace at all — the record was erased precisely when there was
something to record. Delegations now open a unit on the shared work record (`coordination`) with a
deadline, and close it with the answer and the verdict on that answer. A unit still open past its
deadline is the fire-and-forget detector: it means a runner went away without ever writing back.

**Nothing was checked.** Whatever the team lead returned was handed straight on: into the model's
context as a tool result, or — worse — into `_announce_task`, which reads `t.result` out loud. So
"I don't have access to that calendar" came back to the owner as Afon's own finished answer, and a
figure the fleet invented came back in Afon's voice with nothing marking it as somebody else's
claim. The anti-fabrication protocol applies to other agents too; that is the whole of 39.F3.

The check is deliberately not a second research pass. Re-running the work to see if it agrees costs
what the delegation cost and answers a different question. What it asks is whether this is an
answer to *this* brief at all, and whether it asserts specifics Afon has no way to stand behind:

  * **rejected** — empty, or the agent saying it could not do it, or an answer with nothing in
    common with the brief. Not reported as a finding at all.
  * **attributed** — a real answer carrying figures, dates or links that Afon never saw. Reported
    as the team lead's report, in his name.
  * **verified** — an answer to the brief that asserts no unverifiable specifics. Afon may say it
    in his own voice.

Note what is deliberately NOT done: cited URLs are not fed into the citation ledger. Recording them
would mark them as pages this turn retrieved, and the ledger's whole job (41.F2) is to notice when
a reply cites a host nothing opened. Leaving them out is what makes the caveat fire correctly.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from afon.brain import coordination
from afon.config import settings

VERIFIED = "verified"
ATTRIBUTED = "attributed"
REJECTED = "rejected"

#: The agent is the worker on its own delegation. One name, so a row says who was asked.
FLEET = "fleet"

#: A unit is late only once the transport's own ceiling has passed with room to spare — so "late"
#: means the runner never came back at all, not merely that the fleet is thinking.
DEADLINE_SLACK_S = 60.0

#: Things an agent says when it is declining, not answering. Matched near the start only: a long
#: answer that mentions "I can't be certain" halfway through is still an answer.
_DECLINED = (
    "i don't have access", "i do not have access", "i'm unable", "i am unable",
    "i cannot", "i can't", "unable to complete", "i don't have the ability",
    "as an ai", "no information available", "i was not able", "i wasn't able",
    "error:", "failed to",
)
_DECLINE_WINDOW = 240

#: Specifics an answer asserts that Afon has no way to check: a link, a year, a decimal, a figure
#: with a unit or a currency on it.
_SPECIFIC = re.compile(
    r"https?://"
    r"|\b(19|20)\d{2}\b"
    r"|\b\d+[.,]\d+\b"
    r"|[$€£]\s?\d"
    r"|\b\d[\d,]*\s?(%|percent|eur|usd|gbp|k|m|bn|million|billion|kg|km|hours?|days?)\b",
    re.I,
)

_STOPWORDS = {
    "about", "after", "again", "against", "please", "could", "would", "should", "there", "their",
    "these", "those", "which", "while", "with", "what", "when", "where", "from", "into", "that",
    "this", "your", "have", "been", "will", "then", "than", "find", "give", "tell", "make", "need",
    "want", "look", "check", "the", "and", "for", "you", "me", "a", "an", "of", "to", "in", "on",
    "out", "up", "is", "it", "be", "do", "how", "why", "who",
}


@dataclass(frozen=True)
class Verdict:
    """What the delegated answer may be used for."""

    grade: str
    why: str = ""

    @property
    def usable(self) -> bool:
        return self.grade != REJECTED

    def __bool__(self) -> bool:
        return self.usable


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if w not in _STOPWORDS}


def check(brief: str, result: str) -> Verdict:
    """Grade a delegated answer before any of it is reported as fact."""
    text = (result or "").strip()
    if len(text) < 15:
        return Verdict(REJECTED, "the team lead came back with nothing")

    head = text[:_DECLINE_WINDOW].lower()
    for phrase in _DECLINED:
        if phrase in head:
            return Verdict(REJECTED, "the team lead said he couldn't do it")

    want = _content_words(brief)
    if len(want) >= 3 and not (want & _content_words(text)):
        return Verdict(REJECTED, "the answer doesn't address anything the brief asked about")

    if _SPECIFIC.search(text):
        return Verdict(ATTRIBUTED, "it carries figures or links you didn't see for yourself")
    return Verdict(VERIFIED, "")


# --- tracking -------------------------------------------------------------------------------


def _deadline_s(timeout_s: float | None = None) -> float:
    return float(timeout_s or settings.openclaw_request_timeout_seconds) + DEADLINE_SLACK_S


def start(brief: str, *, to: str = FLEET, timeout_s: float | None = None,
          path=None) -> str:
    """Open a tracked unit for this delegation and return its id ('' if the record is unwritable).

    Claimed immediately: the delegation and its worker are the same act, so there is no window in
    which the unit is posted but unowned.
    """
    ids = coordination.post(brief, deadline_s=_deadline_s(timeout_s), path=path)
    if not ids:
        return ""
    unit = coordination.claim(coordination.get(ids[0], path=path).job, to, path=path)
    return unit.id if unit else ""


def finish(unit_id: str, brief: str, result: str, *, to: str = FLEET, path=None) -> Verdict:
    """Check the answer, record it against the unit, and return the verdict."""
    verdict = check(brief, result)
    if unit_id:
        coordination.finish(unit_id, to, result, verdict=verdict.grade, path=path)
    return verdict


def failed(unit_id: str, error: str, *, to: str = FLEET, path=None) -> None:
    if unit_id:
        coordination.fail(unit_id, to, error, path=path)


def outstanding(path=None) -> list[coordination.Unit]:
    """Delegations still open. Anything `late()` here was never written back — fire-and-forget."""
    return coordination.unfinished(path=path)


# --- reporting ------------------------------------------------------------------------------


def for_model(verdict: Verdict, result: str) -> str:
    """The tool result. Tells the model what it is allowed to do with these words."""
    if not verdict.usable:
        return (f"DELEGATION_RETURNED_NOTHING: {verdict.why}. Tell the owner the team lead came "
                "back without an answer, and offer to try it yourself. Do NOT invent an answer and "
                "do NOT present this as a finding.")
    if verdict.grade == ATTRIBUTED:
        return ("DELEGATED_RESULT [attributed] — this is the team lead's report, not your own "
                "finding, and " + verdict.why + ". Say whose it is when you relay it, and don't "
                "restate a number, date or link as something you checked.\n\n" + result)
    return "DELEGATED_RESULT [verified] — an answer to the brief, safe to relay in your own "\
           "words.\n\n" + result


def for_owner(verdict: Verdict, result: str) -> str:
    """The spoken version, for a background delegation that finishes while nobody asked."""
    if not verdict.usable:
        return f"The team lead came back without an answer, sir — {verdict.why}."
    if verdict.grade == ATTRIBUTED:
        return "Here's the team lead's report, in his words rather than mine: " + result
    return result


async def tracked(brief: str, call, *, to: str = FLEET, timeout_s: float | None = None,
                  spoken: bool = False, path=None) -> str:
    """Run one delegation on the record: a unit with a deadline, a checked answer, and a close.

    `call()` returns the awaitable that does the delegating. Every path through here closes the
    unit — answered, or failed with the reason — which is what makes an outstanding unit past its
    deadline mean something. `spoken=True` renders for the owner's ears rather than the model.
    """
    unit = start(brief, to=to, timeout_s=timeout_s, path=path)
    try:
        result = await call()
    except Exception as e:  # noqa: BLE001 — recorded, then re-raised for the caller to phrase
        failed(unit, f"{type(e).__name__}: {e}", to=to, path=path)
        raise
    verdict = finish(unit, brief, result, to=to, path=path)
    return for_owner(verdict, result) if spoken else for_model(verdict, result)


def _selfcheck() -> None:
    """ponytail: the one runnable check — the three grades, and a tracked unit closing out."""
    import tempfile
    from pathlib import Path

    brief = "find out what the Lpstrak rabbit farm feed costs this season"
    assert check(brief, "").grade == REJECTED
    assert check(brief, "I don't have access to the farm's supplier records.").grade == REJECTED
    assert check(brief, "The weather in Berlin is mild this week and stays dry.").grade == REJECTED
    assert check(brief, "Rabbit feed costs about 19.50 per sack this season.").grade == ATTRIBUTED
    v = check(brief, "Feed for the rabbit farm is a little dearer than last season, sir.")
    assert v.grade == VERIFIED, v

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        p = Path(d) / "work.sqlite"
        uid = start(brief, timeout_s=1, path=p)
        assert uid, "the unit was not tracked"
        assert outstanding(path=p), "a live delegation is not outstanding"
        time.sleep(0.01)
        got = finish(uid, brief, "Rabbit feed is 19.50 a sack.", path=p)
        assert got.grade == ATTRIBUTED, got
        assert coordination.get(uid, path=p).verdict == ATTRIBUTED
        assert not outstanding(path=p), "a finished delegation is still outstanding"
    print("delegation self-check ok")


if __name__ == "__main__":
    _selfcheck()
