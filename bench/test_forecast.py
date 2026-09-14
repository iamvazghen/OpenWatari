"""46.F1-F3 — one predictor, scored against a baseline, and silent until it has earned a voice.

`anticipation.py` reads what is already in the calendar and reads it back. That is a look-ahead,
not a forecast: it never says anything that could turn out to be wrong, so it can never be checked.

  [predictor]      will today's plan fit the day, from calendar minutes plus open tasks, computed
                   in-process with no model call, and carrying the assumptions it rests on;
  [scoring ledger] every prediction is written down before the day happens and scored after, and
                   accuracy is always reported beside the naive baseline — "72% right" means
                   nothing if always saying "it fits" also scores 72%;
  [suppression]    below the confidence floor nothing is said. Not hedged, not softened: nothing.

The check that matters most is that a brand-new predictor cannot speak. The first draft of the
confidence function ADDED its history term instead of multiplying, so an obvious-looking day was
spoken with a track record of nothing.

Hermetic: a temporary ledger. No network, no model.

    uv run python bench/test_forecast.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def _mature(F, led: Path, n: int = 12, outcome: str | None = None) -> None:
    """Give the predictor a track record, so the maturity term stops holding it back."""
    for i in range(n):
        p = F.predict_day_fit(busy_minutes=600, open_tasks=2, available_minutes=480,
                              day=f"2026-07-{i + 1:02d}", path=led)
        F.record(p, led)
        F.score(p.key, outcome or p.verdict, led)


def main() -> None:
    from afon.brain import forecast as F

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        led = Path(d) / "forecasts.jsonl"

        print("[predictor] 46.F1 — committed minutes against available ones")
        p = F.predict_day_fit(busy_minutes=120, open_tasks=2, available_minutes=480, path=led)
        check("a quiet day fits", p.verdict == F.FITS, p)
        check("...counting the calendar and the tasks", p.committed_minutes == 180, p)
        check("an overloaded day does not",
              F.predict_day_fit(busy_minutes=600, open_tasks=4, available_minutes=480,
                                path=led).verdict == F.OVERBOOKED)
        check("a day with no slack is neither",
              F.predict_day_fit(busy_minutes=420, open_tasks=0, available_minutes=480,
                                path=led).verdict == F.TIGHT)
        check("the assumption about task length is stated, not buried",
              any("assumed 30 minutes" in b for b in p.basis), p.basis)
        check("...and the calendar half is named too",
              any("minutes of calendar" in b for b in p.basis), p.basis)
        check("an empty day is not a division by zero",
              F.predict_day_fit(available_minutes=0, path=led).verdict == F.FITS)
        check("the key names the day, so it can be scored later", p.key.startswith("day-fit:"), p.key)

        print("\n[predictor] it reads the calendar the same way the clash report does")
        timed = [{"start": {"dateTime": "2026-09-13T09:00:00+00:00"},
                  "end": {"dateTime": "2026-09-13T10:30:00+00:00"}}]
        allday = [{"start": {"date": "2026-09-13"}, "end": {"date": "2026-09-14"}}]
        check("a timed event counts its minutes", F.busy_minutes(timed) == 90, F.busy_minutes(timed))
        check("an all-day entry is not busy time", F.busy_minutes(allday) == 0,
              "someone's birthday must not make the day overbooked")
        check("an unparseable event is skipped rather than crashing",
              F.busy_minutes([{"start": {}, "end": {}}]) == 0)

        print("\n[suppression] 46.F3 — a new predictor does not get to talk")
        fresh = Path(d) / "fresh.jsonl"
        blunt = F.predict_day_fit(busy_minutes=900, open_tasks=6, available_minutes=480,
                                  path=fresh)
        check("even a wildly overbooked day is below the floor with no history",
              not blunt.speakable, blunt.confidence)
        check("...and says nothing at all", blunt.spoken() == "", blunt.spoken())
        check("...rather than a hedged version of the same claim",
              "might" not in blunt.spoken() and "possibly" not in blunt.spoken())

        _mature(F, fresh)
        loud = F.predict_day_fit(busy_minutes=900, open_tasks=6, available_minutes=480, path=fresh)
        check("with a track record it speaks", loud.speakable, loud.confidence)
        check("...and says which way", "doesn't fit" in loud.spoken(), loud.spoken())
        check("...with the numbers behind it", "900" in loud.spoken() or "1080" in loud.spoken(),
              loud.spoken())
        check("...and what it was based on", "Based on" in loud.spoken(), loud.spoken())

        borderline = F.predict_day_fit(busy_minutes=480, open_tasks=0, available_minutes=480,
                                       path=fresh)
        check("a day right on the line stays quiet even with history",
              not borderline.speakable, borderline.confidence)
        check("...because that answer turns on a rounding error", borderline.verdict == F.TIGHT)

        print("\n[scoring ledger] 46.F2 — written before, scored after")
        F.record(p, led)
        rows = F.rows(led)
        check("the prediction is on the record", len(rows) == 1, rows)
        check("...with its confidence", rows[0]["confidence"] == p.confidence, rows[0])
        check("...and what it rested on", rows[0]["basis"] == p.basis, rows[0])
        check("...and no outcome yet", rows[0]["outcome"] == "", rows[0])
        check("an unscored prediction does not count toward accuracy",
              F.accuracy(led)["scored"] == 0, F.accuracy(led))

        check("scoring it works", F.score(p.key, F.FITS, led))
        check("...and it is now scored", F.accuracy(led)["scored"] == 1, F.accuracy(led))
        check("scoring an unknown key changes nothing", not F.score("day-fit:1999-01-01", F.FITS, led))
        check("an invalid outcome is refused", not F.score(p.key, "probably", led))
        check("scoring twice does not double-count", not F.score(p.key, F.FITS, led))

        print("\n[scoring ledger] a quiet prediction is still recorded, or accuracy flatters")
        quiet = Path(d) / "quiet.jsonl"
        weak = F.predict_day_fit(busy_minutes=900, open_tasks=6, available_minutes=480, path=quiet)
        check("(control) it is below the floor", not weak.speakable)
        F.record(weak, quiet)
        check("it went into the ledger anyway", len(F.rows(quiet)) == 1, F.rows(quiet))
        check("the code says why", "flatter" in (Path(__file__).resolve().parents[1] /
              "src/afon/brain/forecast.py").read_text(encoding="utf-8"),
              "scoring only the confident ones would make the accuracy figure meaningless")

        print("\n[scoring ledger] the baseline is reported beside the score, always")
        good = Path(d) / "good.jsonl"
        _mature(F, good, n=10)                      # 10 right, all overbooked
        a = F.accuracy(good)
        check("accuracy counts what was right", a["correct"] == 10 and a["scored"] == 10, a)
        check("...and what always-says-fits would have scored", a["baseline"] == 0.0, a)
        check("...and whether that was beaten", a["beats_baseline"] is True, a)
        said = F.spoken_accuracy(good)
        check("the spoken version names the baseline", "always saying the day fits" in said, said)

        flat = Path(d) / "flat.jsonl"
        _mature(F, flat, n=10, outcome=F.FITS)      # every day actually fitted; it said overbooked
        b = F.accuracy(flat)
        check("a predictor that is always wrong scores zero", b["correct"] == 0, b)
        check("...against a baseline that would have been perfect", b["baseline"] == 1.0, b)
        check("...and is reported as NOT beating it", b["beats_baseline"] is False, b)
        check("...in words", "no better than" in F.spoken_accuracy(flat), F.spoken_accuracy(flat))
        check("no scores at all is said plainly, not as zero percent",
              "no idea whether I'm any good" in F.spoken_accuracy(Path(d) / "none.jsonl"))

        print("\n[scoring ledger] a torn line does not lose the rest")
        with led.open("a", encoding="utf-8") as fh:
            fh.write('{"key": "half')
        check("the ledger still reads", len(F.rows(led)) == 1, F.rows(led))
        check("...and still scores", F.accuracy(led)["scored"] == 1, F.accuracy(led))

    print("\n[predictor] it runs where the owner already asks about his day")
    day_shape = (Path(__file__).resolve().parents[1]
                 / "src/afon/brain/tools/day_shape.py").read_text(encoding="utf-8")
    check("day_clashes carries the forecast", "_forecast_line(events)" in day_shape)
    check("...recording it whether or not it speaks", "forecast.record(p)" in day_shape)
    check("...and never breaking the answer it decorates",
          "except Exception" in day_shape.split("def _forecast_line")[1])
    check("no new per-turn tool was added for it",
          "predict_day_fit" not in day_shape.split("SCHEMAS = [")[1],
          "a schema slot is charged on every turn, asked or not")
    from afon.brain.inventory import by_name

    check("the ledger is declared in the data inventory", by_name("forecasts.jsonl") is not None)
    src = (Path(__file__).resolve().parents[1]
           / "src/afon/brain/forecast.py").read_text(encoding="utf-8")
    check("no model is called on the forecast path", "llm" not in src.lower().split('"""')[2])
    check("the declined alternatives are recorded, with why",
          "Prophet" in src and "decoration" in src)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
