"""47.F1-F3 — one table, one category, and an unknown that stays unknown.

  [store]      add, consume and query against a local sqlite table, seeded with the one category
               Afon can fill in without asking anybody: his own consumables;
  [history]    every consumption is recorded with its date, because without that depletion is not
               estimable at all — and an estimate that cannot be supported is refused with a
               reason rather than produced anyway;
  [no invention] an item nobody recorded is UNKNOWN, not zero. "You have none" and "I don't know"
               send a person to different places, and an inventory that turns the second into the
               first will be believed.

Hermetic: a temporary sqlite file. No network.

    uv run python bench/test_inventory.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
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


def main() -> None:
    from afon.brain import stock as S

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        db = Path(d) / "stock.sqlite"

        print("[store] 47.F1 — add, consume, query")
        got = S.add("feed", 40, unit="sacks", category="farm", location="the lower barn", path=db)
        check("adding records the item", got is not None and got.quantity == 40, got)
        check("...with its unit", got.unit == "sacks", got)
        check("...and where it is", got.location == "the lower barn", got)
        check("adding again accumulates", S.add("feed", 10, path=db).quantity == 50)
        check("...keeping the unit it already had", S.get("feed", path=db).unit == "sacks")
        check("consuming takes it off", S.consume("feed", 5, path=db).quantity == 45)
        check("a query answers in words", "feed: 45 sacks in the lower barn"
              in S.spoken("feed", path=db).lower(), S.spoken("feed", path=db))
        check("stock cannot go below zero", S.consume("feed", 1000, path=db).quantity == 0)
        check("listing a category returns its items",
              [i.name for i in S.items("farm", path=db)] == ["feed"], S.items("farm", path=db))
        check("listing an empty category says so, rather than listing everything",
              "not tracking anything" in S.spoken(category="garage", path=db),
              S.spoken(category="garage", path=db))
        check("an empty name is refused", S.add("  ", 1, path=db) is None)

        print("\n[no invention] 47.F3 — unknown is unknown")
        check("an unrecorded item has no record", S.get("hay", path=db) is None)
        check("consuming it is refused, not invented", S.consume("hay", 1, path=db) is None)
        check("...and it is still unrecorded afterwards", S.get("hay", path=db) is None)
        said = S.spoken("hay", path=db)
        check("the answer is 'I don't know', not 'none'", "nothing recorded" in said, said)
        check("...and says so explicitly", "isn't the same as none" in said, said)
        check("...and never states a quantity", "0" not in said, said)
        days, why = S.depletion("hay", path=db)
        check("no depletion is offered for an unknown item", days is None)
        check("...with the reason being that it is unrecorded", "nothing recorded" in why, why)

        print("\n[history] 47.F2 — consumption carries its date")
        rows = S.history("feed", path=db)
        check("every change is on the record", len(rows) == 4, rows)
        check("...each with a timestamp", all(at > 0 for at, _, _ in rows), rows)
        check("...and a sign that says which way it went",
              [d > 0 for _, d, _ in rows] == [True, True, False, False], rows)
        check("...and what it was", {n for _, _, n in rows} == {"added", "consumed"}, rows)

        print("\n[history] a rate needs history, and is refused until there is some")
        fresh = Path(d) / "rate.sqlite"
        S.add("oil", 100, unit="litres", path=fresh)
        days, why = S.depletion("oil", path=fresh)
        check("no uses yet, so no estimate", days is None)
        check("...and it says why", "nothing recorded being used" in why, why)
        S.consume("oil", 10, path=fresh)
        days, why = S.depletion("oil", path=fresh)
        check("one use is still not a rate", days is None, days)
        check("...and the reason names the sample size", "only 1 recorded use" in why, why)
        S.consume("oil", 10, path=fresh)
        days, why = S.depletion("oil", path=fresh)
        check("two uses inside an hour is still not a rate", days is None, days)
        check("...because a busy hour is not a run rate", "within an hour" in why, why)

        # Now separate them properly: 20 litres over 10 days against 80 left is 40 days.
        with S._open(fresh) as c:
            c.execute("UPDATE events SET at=? WHERE rowid=(SELECT MIN(rowid) FROM events "
                      "WHERE delta<0)", (time.time() - 10 * 86400,))
        days, why = S.depletion("oil", path=fresh)
        check("with real history there is an estimate", days is not None, why)
        check("...and it is arithmetic anyone can check", days and 35 < days < 45, days)
        said = S.spoken("oil", path=fresh)
        check("the spoken answer carries it", "days left" in said, said)

        print("\n[store] the category Afon can fill in without asking")
        seeded = Path(d) / "seed.sqlite"
        backups = Path(d) / "backups"
        backups.mkdir()
        (backups / "one.tar.gz").write_text("x", encoding="utf-8")
        made = S.seed_self(path=seeded, root=Path(d), backups=backups)
        names = {i.name for i in made}
        check("it seeds real, measured numbers", {"disk free", "backup archives"} <= names, names)
        check("...under one category", all(i.category == S.SELF for i in made), made)
        check("...with the archive count it actually found",
              S.get("backup archives", path=seeded).quantity == 1)
        check("a second reading replaces rather than stacks",
              S.seed_self(path=seeded, root=Path(d), backups=backups) and
              S.get("backup archives", path=seeded).quantity == 1,
              S.get("backup archives", path=seeded))
        (backups / "two.tar.gz").write_text("x", encoding="utf-8")
        S.seed_self(path=seeded, root=Path(d), backups=backups)
        check("...and a change is logged, so a rate can form",
              any(n == "measured" for _, _, n in S.history("backup archives", path=seeded)),
              S.history("backup archives", path=seeded))

        print("\n[store] the tools")
        from afon.brain.tools import groups_for_text, tool_handlers
        from afon.brain.tools.stock import check_stock, update_stock

        check("both tools are registered",
              {"check_stock", "update_stock"} <= set(tool_handlers()))
        for phrase in ("how much feed do we have", "are we running low on sacks",
                       "we used five sacks today", "forty more came in"):
            check(f"'{phrase}' reaches the group", "stock" in groups_for_text(phrase),
                  groups_for_text(phrase))
        check("an ordinary turn does not load it", "stock" not in groups_for_text("what's the time"))

        real = S._path()
        try:
            S._path = lambda path=None: Path(path) if path else db      # type: ignore[assignment]
            said = asyncio.run(check_stock({"item": "feed"}))
            check("check_stock answers from the store", "feed:" in said.lower(), said)
            said = asyncio.run(update_stock({"item": "feed", "change": 12}))
            check("a positive change adds", "57" in said or "12" in said, said)
            said = asyncio.run(update_stock({"item": "nothing at all", "change": -1}))
            check("using an unknown item is refused in the tool too",
                  "nothing recorded" in said, said)
            check("...and offers the way forward", "count down from there" in said, said)
            said = asyncio.run(update_stock({"item": "feed", "change": "lots"}))
            check("a non-number is asked about, not guessed", "Give me a number" in said, said)
        finally:
            S._path = real                                              # type: ignore[assignment]

    print("\n[store] the store is declared and opened the one permitted way")
    from afon.brain.inventory import by_name

    check("the store is in the data inventory", by_name("afon_stock.sqlite") is not None)
    src = (Path(__file__).resolve().parents[1]
           / "src/afon/brain/stock.py").read_text(encoding="utf-8")
    check("it goes through dbconn.connect", "from afon.brain.dbconn import connect" in src)
    check("...and not sqlite3.connect directly", "sqlite3.connect" not in src)
    hyg = (Path(__file__).resolve().parents[1]
           / "src/afon/brain/maintenance.py").read_text(encoding="utf-8")
    check("a reading is taken by the job that already runs daily", "seed_self()" in hyg)

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
