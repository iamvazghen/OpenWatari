"""46.F1-F3 — one predictor, scored, and silent until it has earned the right to speak.

`anticipation.py` is a calendar look-ahead: it reads what is already written down and reads it
back. That is not a forecast, and calling it one is the thing this system exists to stop. A
forecast says something that is not in the data yet and then finds out whether it was right.

**One predictor, not a framework.** Will today's plan fit the day: committed minutes against
available ones. The plan declined Prophet, Chronos and the rest until a naive baseline is beaten,
and the reason holds — a foundation forecasting model over thirty days of one person's calendar is
decoration. So the baseline is here too, in the ledger, as the thing to beat: **always say it
fits**. That is what most days do, and any predictor that cannot beat it is worse than nothing
while being more expensive and more believable.

**The ledger is the point, not the prediction.** Every prediction is written down with what it was
based on, and scored against what actually happened. Without that, a forecaster is a thing that
says confident sentences forever and is never wrong in a way anyone can measure.

**And it stays quiet.** Confidence below the floor is not spoken at all. On day one there is no
history, so nothing is spoken — which is the correct behaviour and not a degraded one. A predictor
that starts talking before it has been right about anything is asking to be believed on credit.

ponytail: arithmetic and a JSONL, no statsmodels. `statsforecast` goes in when the ledger shows the
baseline being beaten and a seasonal term would add something; fitting a model to a dozen rows
would be ceremony.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path

from afon.shared.paths import state_dir

FITS = "fits"
TIGHT = "tight"
OVERBOOKED = "overbooked"

#: Below this, nothing is said out loud (46.F3). Set where it is because a prediction the owner
#: hears is one he will plan around, and being talked into rearranging a day on a coin flip is
#: worse than hearing nothing at all.
CONFIDENCE_FLOOR = 0.55

#: What one open task with no estimate is assumed to cost. A STATED assumption — it appears in the
#: basis so the owner can see the prediction rests on it, rather than being buried as a constant.
DEFAULT_TASK_MINUTES = 30

#: A working day, when nothing better is known. Same rule: stated, and visible in the basis.
DEFAULT_AVAILABLE_MINUTES = 8 * 60

#: How many scored days before history contributes confidence. Below this the predictor is honest
#: that it is guessing, which is why nothing is spoken in the first fortnight.
MATURE_AFTER = 10

#: Committed / available. Above `TIGHT_AT` the day has no slack; above `OVER_AT` it does not fit.
TIGHT_AT = 0.85
OVER_AT = 1.0


def _ledger() -> Path:
    return state_dir() / "forecasts.jsonl"


@dataclass(frozen=True)
class Prediction:
    """What was predicted, what it rested on, and how sure it was."""

    key: str
    verdict: str
    confidence: float
    committed_minutes: float = 0.0
    available_minutes: float = 0.0
    basis: list[str] = field(default_factory=list)
    kind: str = "day-fit"
    at: float = field(default_factory=time.time)

    @property
    def speakable(self) -> bool:
        return self.confidence >= CONFIDENCE_FLOOR

    @property
    def ratio(self) -> float:
        return self.committed_minutes / self.available_minutes if self.available_minutes else 0.0

    def row(self) -> dict:
        return {"key": self.key, "kind": self.kind, "verdict": self.verdict,
                "confidence": round(self.confidence, 3),
                "committed_minutes": round(self.committed_minutes, 1),
                "available_minutes": round(self.available_minutes, 1),
                "basis": self.basis, "at": self.at}

    def spoken(self) -> str:
        """The sentence, or '' when it is below the floor. Never a hedged version of itself."""
        if not self.speakable:
            return ""
        over = self.committed_minutes - self.available_minutes
        if self.verdict == OVERBOOKED:
            return (f"Today doesn't fit, sir — about {self.committed_minutes:.0f} minutes "
                    f"committed against {self.available_minutes:.0f} available, "
                    f"{over:.0f} over. Based on {', '.join(self.basis)}.")
        if self.verdict == TIGHT:
            return (f"Today fits, sir, but with nothing spare — {self.committed_minutes:.0f} "
                    f"minutes against {self.available_minutes:.0f}. "
                    f"Based on {', '.join(self.basis)}.")
        return (f"Today has room, sir — {self.committed_minutes:.0f} minutes committed of "
                f"{self.available_minutes:.0f}.")


def _verdict(ratio: float) -> str:
    if ratio > OVER_AT:
        return OVERBOOKED
    if ratio >= TIGHT_AT:
        return TIGHT
    return FITS


def _confidence(ratio: float, scored: int) -> float:
    """How sure to be: how far from the boundary, tempered by how much has been scored.

    Both halves matter and they fail differently. A ratio of 0.99 is a coin flip however much
    history exists, because the answer turns on a rounding error in somebody's meeting length. And
    a ratio of 2.0 with no track record is still a claim from a predictor that has never been
    checked, which is exactly the position from which confident forecasting does harm.
    """
    distance = min(abs(ratio - TIGHT_AT), abs(ratio - OVER_AT))
    sharpness = min(1.0, distance / 0.25)            # 0 at the boundary, 1 a quarter away
    maturity = min(1.0, scored / MATURE_AFTER)
    # MULTIPLIED, not added. Added, an obvious-looking day scored high enough to speak on the very
    # first run, with a track record of nothing — which is the precise failure 46.F3 forbids and it
    # survived the first draft of this function. Multiplying makes having been checked a
    # precondition rather than a bonus: no history caps confidence below the floor however
    # clear-cut the arithmetic looks.
    return round((0.5 + 0.5 * sharpness) * (0.4 + 0.6 * maturity), 3)


def predict_day_fit(*, busy_minutes: float = 0.0, open_tasks: int = 0,
                    task_minutes: int = DEFAULT_TASK_MINUTES,
                    available_minutes: float = DEFAULT_AVAILABLE_MINUTES,
                    day: str = "", path: Path | None = None) -> Prediction:
    """Will today's plan fit the day. Arithmetic, in-process, no model call."""
    day = day or _date.today().isoformat()
    committed = max(0.0, float(busy_minutes)) + max(0, int(open_tasks)) * float(task_minutes)
    available = max(1.0, float(available_minutes))
    basis = [f"{busy_minutes:.0f} minutes of calendar"]
    if open_tasks:
        basis.append(f"{open_tasks} open task{'s' if open_tasks != 1 else ''} at an assumed "
                     f"{task_minutes} minutes each")
    scored = len(scored_rows(path))
    ratio = committed / available
    return Prediction(key=f"day-fit:{day}", verdict=_verdict(ratio),
                      confidence=_confidence(ratio, scored),
                      committed_minutes=committed, available_minutes=available, basis=basis)


def busy_minutes(events: list[dict]) -> float:
    """Timed minutes in a day's calendar. Uses `schedule._parse`, so the forecast and the clash
    report cannot disagree about which events are real — an all-day birthday is not busy time."""
    from afon.brain.schedule import _parse

    total = 0.0
    for ev in events or []:
        parsed = _parse(ev)
        if parsed:
            start, end, _, _ = parsed
            total += (end - start).total_seconds() / 60.0
    return round(total, 1)


# --- the ledger ------------------------------------------------------------------------------


def record(prediction: Prediction, path: Path | None = None) -> None:
    """Write the prediction down BEFORE the day happens. Silent predictions are recorded too.

    A prediction that was too weak to say is still a prediction, and dropping those would make the
    accuracy figure flattering: only the confident ones would ever be scored.
    """
    p = path or _ledger()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({**prediction.row(), "outcome": ""}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def rows(path: Path | None = None) -> list[dict]:
    try:
        text = (path or _ledger()).read_text(encoding="utf-8")
    except OSError:
        return []
    out = []
    for line in text.splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def scored_rows(path: Path | None = None) -> list[dict]:
    return [r for r in rows(path) if r.get("outcome")]


def score(key: str, outcome: str, path: Path | None = None) -> bool:
    """Record what actually happened for one prediction. Rewrites the ledger in place."""
    if outcome not in (FITS, TIGHT, OVERBOOKED):
        return False
    p = path or _ledger()
    all_rows = rows(p)
    hit = False
    for r in all_rows:
        if r.get("key") == key and not r.get("outcome"):
            r["outcome"] = outcome
            hit = True
    if not hit:
        return False
    try:
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in all_rows) + "\n",
                     encoding="utf-8")
    except OSError:
        return False
    return True


def accuracy(path: Path | None = None) -> dict:
    """How often it was right, and how often ALWAYS-SAYING-FITS would have been.

    The baseline is reported beside the predictor, always, because "72% correct" means nothing on
    its own — if most days fit, a constant answer scores 72% too and costs nothing to run.
    """
    scored = scored_rows(path)
    if not scored:
        return {"scored": 0, "correct": 0, "rate": None, "baseline": None, "beats_baseline": None}
    correct = sum(1 for r in scored if r.get("verdict") == r.get("outcome"))
    naive = sum(1 for r in scored if r.get("outcome") == FITS)
    rate = correct / len(scored)
    base = naive / len(scored)
    return {"scored": len(scored), "correct": correct, "rate": round(rate, 3),
            "baseline": round(base, 3), "beats_baseline": rate > base}


def spoken_accuracy(path: Path | None = None) -> str:
    a = accuracy(path)
    if not a["scored"]:
        return "I haven't scored a single prediction yet, sir, so I've no idea whether I'm any good."
    verdict = "better than" if a["beats_baseline"] else "no better than"
    return (f"Of {a['scored']} scored predictions I got {a['correct']} right — {a['rate']:.0%}, "
            f"which is {verdict} simply always saying the day fits ({a['baseline']:.0%}).")


def _selfcheck() -> None:
    """ponytail: the one runnable check — silence without history, and a baseline to beat."""
    import tempfile

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        led = Path(d) / "forecasts.jsonl"
        p = predict_day_fit(busy_minutes=600, open_tasks=4, available_minutes=480, day="2026-09-13",
                            path=led)
        assert p.verdict == OVERBOOKED, p
        assert not p.speakable and p.spoken() == "", p          # no history yet -> silent
        record(p, led)
        assert score(p.key, OVERBOOKED, led)
        assert not score("day-fit:nope", FITS, led)
        a = accuracy(led)
        assert a["scored"] == 1 and a["correct"] == 1 and a["baseline"] == 0.0, a

        for i in range(12):
            q = predict_day_fit(busy_minutes=700, open_tasks=2, available_minutes=480,
                                day=f"2026-08-{i + 1:02d}", path=led)
            record(q, led)
            score(q.key, OVERBOOKED, led)
        loud = predict_day_fit(busy_minutes=700, open_tasks=2, available_minutes=480,
                               day="2026-09-14", path=led)
        assert loud.speakable and "doesn't fit" in loud.spoken(), loud
        assert accuracy(led)["beats_baseline"], accuracy(led)
    print("forecast self-check ok")


if __name__ == "__main__":
    _selfcheck()
