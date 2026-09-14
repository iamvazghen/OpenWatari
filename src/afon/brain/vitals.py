"""34.F1/34.F3 — real vitals from a file the owner already has, inside a stated boundary.

The fitness tools were written against the Google Fit API, which needs an enablement in a Cloud
project that has never been done — so for months the capability existed and returned "not
configured yet" to every question. The plan's blocker table retired that dependency for the right
reason: **ingest against an open export format**, and naming the watch later changes no code.

So this reads Apple Health's `export.xml`, which every iPhone produces from the Health app with no
developer account, no API, no consent screen and no key. Read with `iterparse` because a real
export is hundreds of megabytes and the file must never be loaded whole; nothing here depends on
which device wrote it.

**And there is a boundary.** 34.F3 asks that health talk stay inside one, and it has to live
somewhere a reader will find it rather than in a docstring: `BOUNDARY` is stated here, carried into
the tool descriptions, and asserted by the gate. Afon reports observations and patterns — what the
numbers say, and what has changed. He does not name conditions, does not explain causes, and does
not advise on treatment, because every one of those is a claim a person may act on instead of
asking somebody qualified. The distinction is not squeamishness: "you slept five hours, three
nights running" is a fact he measured, and "you're probably not sleeping because of X" is a
diagnosis he invented.
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

#: Said whenever health is discussed, and carried in the tool descriptions so the model has it in
#: front of it rather than in training. Deliberately short: a boundary nobody can recite is not one.
BOUNDARY = ("I report what your data says and what has changed in it. I don't name conditions, "
            "explain causes, or advise on treatment — that's for someone qualified, with your "
            "actual history in front of them.")

#: The Apple Health record types worth reading, mapped to what they are in plain words.
TYPES: dict[str, str] = {
    "HKQuantityTypeIdentifierStepCount": "steps",
    "HKQuantityTypeIdentifierHeartRate": "heart rate",
    "HKQuantityTypeIdentifierRestingHeartRate": "resting heart rate",
    "HKCategoryTypeIdentifierSleepAnalysis": "sleep",
    "HKQuantityTypeIdentifierBodyMass": "weight",
    "HKQuantityTypeIdentifierActiveEnergyBurned": "active energy",
}

#: Words that turn an observation into a diagnosis. Used to check Afon's own phrasing, never the
#: owner's — he may say whatever he likes about his own health.
DIAGNOSTIC = (
    "you have ", "you've got ", "you are suffering", "you're suffering", "this is caused by",
    "that's caused by", "the cause is", "diagnos", "you should take", "you need to take",
    "i'd prescribe", "prescribe", "it's a symptom of", "symptom of", "you're anaemic",
    "you're anemic", "you have a deficiency", "indicates that you have", "sign of ",
)

_APPLE_TS = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})")


@dataclass
class Reading:
    kind: str
    value: float
    unit: str = ""
    at: float = 0.0

    def day(self) -> str:
        return datetime.fromtimestamp(self.at).strftime("%Y-%m-%d") if self.at else ""


@dataclass
class Vitals:
    """What was read out of an export, and what could not be."""

    readings: list[Reading] = field(default_factory=list)
    skipped: int = 0
    kinds: dict[str, int] = field(default_factory=dict)
    source: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def by_day(self, kind: str) -> dict[str, float]:
        """Daily totals for one kind — steps sum, heart rate averages. Empty if nothing was read."""
        rows = [r for r in self.readings if r.kind == kind]
        if not rows:
            return {}
        out: dict[str, list[float]] = {}
        for r in rows:
            out.setdefault(r.day(), []).append(r.value)
        if kind in ("steps", "active energy"):
            return {d: round(sum(v), 1) for d, v in sorted(out.items())}
        return {d: round(sum(v) / len(v), 1) for d, v in sorted(out.items())}

    def spoken(self) -> str:
        if self.error:
            return f"I couldn't read that health export, sir — {self.error}."
        if not self.readings:
            return ("That export had nothing I recognise in it, sir — no steps, sleep or heart "
                    "rate records.")
        bits = ", ".join(f"{n} {k} readings" for k, n in sorted(self.kinds.items()))
        said = f"Read {len(self.readings)} records from your export, sir: {bits}."
        if self.skipped:
            said += f" I skipped {self.skipped} I couldn't parse."
        return said + " " + BOUNDARY


def read_apple_health(path: str | Path, *, since: float = 0.0, limit: int = 200_000) -> Vitals:
    """Stream an Apple Health `export.xml` into dated readings. Never loads the file whole.

    A real export is hundreds of megabytes of `<Record>` elements. `iterparse` with an explicit
    `elem.clear()` keeps the memory flat; reading it with `ET.parse` works on a test fixture and
    then takes the brain down on the owner's actual file, which is the worst possible place to
    find out.
    """
    p = Path(path)
    out = Vitals(source=str(p))
    if not p.is_file():
        out.error = f"no file at {p}"
        return out
    try:
        for _event, elem in ET.iterparse(str(p), events=("end",)):
            if elem.tag != "Record":
                continue
            try:
                kind = TYPES.get(elem.get("type") or "")
                if kind is None:
                    continue
                at = _parse_time(elem.get("startDate") or "")
                value = _parse_value(elem.get("value") or "", kind)
                if at <= since or value is None:
                    continue
                out.readings.append(Reading(kind, value, elem.get("unit") or "", at))
                out.kinds[kind] = out.kinds.get(kind, 0) + 1
            except Exception:  # noqa: BLE001 — one bad record must not lose the export
                out.skipped += 1
            finally:
                elem.clear()
            if len(out.readings) >= limit:
                break
    except ET.ParseError as e:
        out.error = f"the file isn't valid XML ({e})"
    except OSError as e:
        out.error = f"{type(e).__name__}"
    return out


def _parse_time(raw: str) -> float:
    m = _APPLE_TS.match(raw or "")
    if not m:
        return 0.0
    try:
        return time.mktime(time.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return 0.0


def _parse_value(raw: str, kind: str) -> float | None:
    if kind == "sleep":
        # Sleep records carry a state, not a number. One record is one asleep interval; the useful
        # scalar is its presence, so count it as 1 and let `by_day` sum the nights.
        return 1.0 if "Asleep" in raw else None
    try:
        return float(raw)
    except ValueError:
        return None


def _store(path: Path | None = None) -> Path:
    from afon.shared.paths import state_dir

    return Path(path) if path else state_dir() / "vitals.json"


def save(v: Vitals, path: Path | None = None) -> dict:
    """Merge an import into the kept daily figures. Returns the store as it now stands.

    Daily aggregates rather than the raw records: an export is hundreds of thousands of rows and
    keeping them would put a copy of the owner's entire health history in the state root to answer
    "how did I sleep last week". A day's steps is the question; the export stays his.
    """
    import json

    p = _store(path)
    try:
        have = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        have = {}
    for kind in v.kinds:
        have.setdefault(kind, {}).update({d: n for d, n in v.by_day(kind).items()})
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(have, indent=1, sort_keys=True), encoding="utf-8")
    except OSError:
        pass
    return have


def kept(kind: str = "", path: Path | None = None) -> dict:
    """The daily figures on file — everything, or one kind."""
    import json

    try:
        have = json.loads(_store(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return have.get(kind, {}) if kind else have


def recent(kind: str, days: int = 7, path: Path | None = None) -> str:
    """A spoken line for one kind over the last `days`, always inside the boundary."""
    rows = sorted(kept(kind, path).items())[-days:]
    if not rows:
        return (f"I've no {kind} on file, sir — import a health export and I'll have something "
                "to go on.")
    latest = rows[-1]
    if len(rows) == 1:
        return f"Your {kind} on {latest[0]}: {latest[1]:g}, sir. That's the only day I have."
    others = [v for _, v in rows[:-1]]
    avg = sum(others) / len(others)
    direction = "above" if latest[1] > avg else "below" if latest[1] < avg else "level with"
    return (f"Your {kind} on {latest[0]} was {latest[1]:g}, sir — {direction} your "
            f"{len(others)}-day average of {avg:.1f}. {BOUNDARY}")


def diagnostic_language(text: str) -> list[str]:
    """Phrases in AFON's own words that cross from observation into diagnosis. Empty is the pass.

    Checks what Afon says, never what the owner says: he is entitled to describe his own health in
    any terms he likes, and a guard that policed his speech would be both useless and insulting.
    """
    low = (text or "").lower()
    return [w.strip() for w in DIAGNOSTIC if w in low]


def _selfcheck() -> None:
    """ponytail: the one runnable check — a real-shaped export, and the boundary that holds."""
    import tempfile

    sample = """<?xml version="1.0" encoding="UTF-8"?>
<HealthData locale="en_GB">
 <Record type="HKQuantityTypeIdentifierStepCount" unit="count" startDate="2026-09-11 08:00:00 +0200" value="1200"/>
 <Record type="HKQuantityTypeIdentifierStepCount" unit="count" startDate="2026-09-11 18:00:00 +0200" value="3400"/>
 <Record type="HKQuantityTypeIdentifierRestingHeartRate" unit="count/min" startDate="2026-09-11 07:00:00 +0200" value="58"/>
 <Record type="HKCategoryTypeIdentifierSleepAnalysis" startDate="2026-09-11 00:30:00 +0200" value="HKCategoryValueSleepAnalysisAsleepCore"/>
 <Record type="HKQuantityTypeIdentifierStepCount" unit="count" startDate="bad date" value="99"/>
 <Record type="HKQuantityTypeIdentifierSomethingElse" unit="x" startDate="2026-09-11 09:00:00 +0200" value="1"/>
</HealthData>
"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        p = Path(d) / "export.xml"
        p.write_text(sample, encoding="utf-8")
        v = read_apple_health(p)
        assert v.ok, v.error
        assert v.kinds["steps"] == 2, v.kinds
        assert v.by_day("steps")["2026-09-11"] == 4600.0, v.by_day("steps")
        assert v.by_day("resting heart rate")["2026-09-11"] == 58.0
        assert v.by_day("sleep")["2026-09-11"] == 1.0
        assert BOUNDARY in v.spoken(), v.spoken()
        assert not read_apple_health(Path(d) / "nope.xml").ok

    assert diagnostic_language("You slept five hours, three nights running.") == []
    assert diagnostic_language("That's a sign of anaemia, sir.")
    assert diagnostic_language("You have low iron.")
    print("vitals self-check ok")


if __name__ == "__main__":
    _selfcheck()
