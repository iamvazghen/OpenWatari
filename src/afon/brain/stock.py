"""47.F1-F3 — what the owner has, and when it runs out. One table, one category, no framework.

The plan's first question — which category is worth tracking at all — was the blocker, and the
answer it settled on is the one Afon can fill in without asking anybody: **his own consumables**.
Disk on the state volume and archives in the backup folder are real numbers he can read today, so
the table has data on day one rather than being a schema waiting for someone to type into it. A
physical category the owner names later slots into the same three columns.

Two things this refuses to do, and they are the interesting half.

**It never invents stock.** An item nobody has recorded is *unknown*, and unknown is answered as
unknown — not as zero, and not as an estimate from a similar item. "You have none" and "I don't
know" send a person to different places, and an inventory that quietly turns the second into the
first is worse than no inventory, because it will be believed.

**It refuses to estimate depletion it cannot support.** A run rate needs at least two consumption
events far enough apart to mean something. From one reading, any number would be arithmetic on a
sample of one dressed up as a forecast. So `depletion` returns None and says why, and the caller
says so out loud.

ponytail: one sqlite file with two tables and no ORM. Grocy stays in reserve exactly as the plan
records — it is a better inventory app, and it is also another service, another UI and another
source of truth for a table that currently holds four rows.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from afon.brain.dbconn import connect
from afon.shared.paths import state_dir, store_path

#: The category Afon can populate himself. A category the owner names is just another string.
SELF = "afon"

#: A run rate needs this many recorded consumptions before it means anything. Two is not a
#: statistical choice, it is the minimum that makes "per day" a division rather than a guess.
MIN_EVENTS_FOR_RATE = 2

#: And they have to be separated by enough time that the rate is not an artifact of one busy hour.
MIN_SPAN_S = 3600.0


@dataclass(frozen=True)
class Item:
    name: str
    quantity: float
    unit: str = ""
    category: str = ""
    location: str = ""
    updated_at: float = 0.0

    def said(self) -> str:
        qty = f"{self.quantity:g}{(' ' + self.unit) if self.unit else ''}"
        where = f" in {self.location}" if self.location else ""
        return f"{self.name}: {qty}{where}"


def _path(path: str | Path | None = None) -> Path:
    return Path(path) if path else store_path("stock")


def _open(path: str | Path | None = None) -> sqlite3.Connection:
    c = connect(_path(path))
    c.execute("CREATE TABLE IF NOT EXISTS items ("
              "name TEXT PRIMARY KEY, category TEXT, quantity REAL, unit TEXT, location TEXT, "
              "updated_at REAL)")
    c.execute("CREATE TABLE IF NOT EXISTS events ("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, delta REAL, at REAL, note TEXT)")
    return c


def add(name: str, quantity: float, *, unit: str = "", category: str = "", location: str = "",
        path=None) -> Item | None:
    """Put stock in, or set it for the first time. Returns the item as it now stands."""
    name = (name or "").strip()
    if not name:
        return None
    now = time.time()
    try:
        with _open(path) as c:
            row = c.execute("SELECT * FROM items WHERE name=?", (name,)).fetchone()
            have = float(row["quantity"]) if row else 0.0
            c.execute(
                "INSERT OR REPLACE INTO items (name, category, quantity, unit, location, updated_at)"
                " VALUES (?,?,?,?,?,?)",
                (name, category or (row["category"] if row else ""), have + float(quantity),
                 unit or (row["unit"] if row else ""),
                 location or (row["location"] if row else ""), now))
            c.execute("INSERT INTO events (name, delta, at, note) VALUES (?,?,?,'added')",
                      (name, float(quantity), now))
    except (sqlite3.Error, ValueError):
        return None
    return get(name, path=path)


def consume(name: str, quantity: float = 1.0, *, note: str = "", path=None) -> Item | None:
    """Take stock out, recorded with the date (47.F2) — without that, depletion is not estimable.

    Returns None for an item nobody has recorded. Creating it on the way down would invent a
    starting quantity, which is exactly what 47.F3 forbids.
    """
    name = (name or "").strip()
    if not name:
        return None
    existing = get(name, path=path)
    if existing is None:
        return None
    now = time.time()
    try:
        with _open(path) as c:
            c.execute("UPDATE items SET quantity=?, updated_at=? WHERE name=?",
                      (max(0.0, existing.quantity - float(quantity)), now, name))
            c.execute("INSERT INTO events (name, delta, at, note) VALUES (?,?,?,?)",
                      (name, -abs(float(quantity)), now, note or "consumed"))
    except (sqlite3.Error, ValueError):
        return None
    return get(name, path=path)


def get(name: str, *, path=None) -> Item | None:
    """The item, or None if nothing has ever been recorded under that name. None means unknown."""
    try:
        with _open(path) as c:
            r = c.execute("SELECT * FROM items WHERE name=?", ((name or "").strip(),)).fetchone()
    except sqlite3.Error:
        return None
    if r is None:
        return None
    return Item(name=r["name"], quantity=float(r["quantity"] or 0), unit=r["unit"] or "",
                category=r["category"] or "", location=r["location"] or "",
                updated_at=float(r["updated_at"] or 0))


def items(category: str = "", *, path=None) -> list[Item]:
    try:
        with _open(path) as c:
            if category:
                rows = c.execute("SELECT * FROM items WHERE category=? ORDER BY name", (category,))
            else:
                rows = c.execute("SELECT * FROM items ORDER BY category, name")
            return [Item(name=r["name"], quantity=float(r["quantity"] or 0), unit=r["unit"] or "",
                         category=r["category"] or "", location=r["location"] or "",
                         updated_at=float(r["updated_at"] or 0)) for r in rows]
    except sqlite3.Error:
        return []


def history(name: str, *, path=None) -> list[tuple[float, float, str]]:
    """(at, delta, note) for one item, oldest first — the record 47.F2 is about."""
    try:
        with _open(path) as c:
            return [(float(r["at"]), float(r["delta"]), r["note"] or "")
                    for r in c.execute("SELECT * FROM events WHERE name=? ORDER BY at", (name,))]
    except sqlite3.Error:
        return []


def depletion(name: str, *, path=None) -> tuple[float | None, str]:
    """Days until this runs out at the observed rate, or (None, why not).

    The `why not` is the useful half. "I don't have enough history to say" is a real answer and it
    is the honest one from a single reading; a number derived from one event would be arithmetic on
    a sample of one, presented as a forecast.
    """
    item = get(name, path=path)
    if item is None:
        return None, f"I have nothing recorded for '{name}'"
    used = [(at, -delta) for at, delta, _ in history(name, path=path) if delta < 0]
    if len(used) < MIN_EVENTS_FOR_RATE:
        return None, (f"only {len(used)} recorded use of {name}"
                      if len(used) == 1 else f"nothing recorded being used of {name}")
    span = used[-1][0] - used[0][0]
    if span < MIN_SPAN_S:
        return None, f"every use of {name} was recorded within an hour, so there's no rate yet"
    per_day = sum(q for _, q in used) / (span / 86400.0)
    if per_day <= 0:
        return None, f"{name} isn't being used up"
    return item.quantity / per_day, ""


# --- the category Afon can fill in himself ----------------------------------------------------


def seed_self(path=None, *, root: Path | None = None, backups: Path | None = None) -> list[Item]:
    """Record what Afon actually consumes, from numbers he can read. Safe to call repeatedly.

    Measured, not declared. Free disk and archive count are facts on this machine right now, so the
    table has real data the day it exists rather than being a schema waiting for someone to type
    into it — and a real run rate accumulates on its own.
    """
    import shutil

    root = Path(root) if root else state_dir()
    backups = Path(backups) if backups else Path(__file__).resolve().parents[3] / "backups"
    out: list[Item] = []
    try:
        root.mkdir(parents=True, exist_ok=True)
        free_gb = round(shutil.disk_usage(root).free / 1e9, 1)
        out.append(_set(SELF, "disk free", free_gb, "GB", str(root), path=path))
    except OSError:
        pass
    try:
        n = len(list(backups.glob("*.tar*"))) + len(list(backups.glob("*.zip")))
        out.append(_set(SELF, "backup archives", float(n), "archives", str(backups), path=path))
    except OSError:
        pass
    return [i for i in out if i is not None]


def _set(category: str, name: str, quantity: float, unit: str, location: str, *, path=None):
    """Overwrite a measured quantity, and record the CHANGE as an event so a rate can form.

    Distinct from `add`, which accumulates: a reading of free disk replaces the last reading, it
    does not stack on it. The delta is what goes in the log, because the delta is the consumption.
    """
    before = get(name, path=path)
    now = time.time()
    delta = quantity - (before.quantity if before else quantity)
    try:
        with _open(path) as c:
            c.execute(
                "INSERT OR REPLACE INTO items (name, category, quantity, unit, location, updated_at)"
                " VALUES (?,?,?,?,?,?)", (name, category, float(quantity), unit, location, now))
            if before is not None and delta != 0:
                c.execute("INSERT INTO events (name, delta, at, note) VALUES (?,?,?,'measured')",
                          (name, delta, now))
    except sqlite3.Error:
        return None
    return get(name, path=path)


def spoken(name: str = "", *, category: str = "", path=None) -> str:
    """The answer to "how much X have I got" / "what have you got in <category>"."""
    if name:
        item = get(name, path=path)
        if item is None:
            return (f"I've nothing recorded for '{name}', sir — which isn't the same as none. "
                    "Tell me what you have and I'll keep count from there.")
        days, why = depletion(name, path=path)
        tail = ""
        if days is not None:
            tail = f" At the rate you've been using it, about {days:.0f} days left."
        elif why:
            tail = f" No estimate of how long that lasts yet — {why}."
        return item.said().capitalize() + "." + tail
    rows = items(category, path=path)
    if not rows:
        return ("I'm not tracking anything" + (f" under '{category}'" if category else "")
                + " yet, sir.")
    return "Stock, sir: " + "; ".join(r.said() for r in rows) + "."


def _selfcheck() -> None:
    """ponytail: the one runnable check — unknown stays unknown, and a rate needs real history."""
    import tempfile

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        p = Path(d) / "stock.sqlite"
        assert get("feed", path=p) is None
        assert consume("feed", 1, path=p) is None, "consuming invented an item"
        assert add("feed", 40, unit="sacks", category="farm", location="the barn", path=p)
        assert get("feed", path=p).quantity == 40
        days, why = depletion("feed", path=p)
        assert days is None and "nothing recorded being used" in why, (days, why)
        consume("feed", 5, path=p)
        assert get("feed", path=p).quantity == 35
        days, why = depletion("feed", path=p)
        assert days is None and "only 1 recorded use" in why, (days, why)
        # Two uses a week apart: now there is a rate.
        with _open(p) as c:
            c.execute("UPDATE events SET at=? WHERE note='consumed'", (time.time() - 7 * 86400,))
        consume("feed", 5, path=p)
        days, _ = depletion("feed", path=p)
        assert days and 20 < days < 60, days
        assert "isn't the same as none" in spoken("hay", path=p)
    print("stock self-check ok")


if __name__ == "__main__":
    _selfcheck()
