"""40.F1-F3 — what the owner owns, read from a file he controls, and never moved.

Only read-only market lookups existed: a ticker in, a sentence out, nothing that knew what he
actually holds. The plan settled the shape and the reasoning holds up: a **Beancount plain-text
ledger**, because it is diffable, git-versioned, and auditable by him without Afon — and read-only
by construction, which is the property that matters most here. Bank aggregation was declined as a
large credential and consent surface for one person's balances, and write access to money is out of
scope by decision, not by omission.

Beancount is used as a **file format**, never linked: nothing here imports it, so its GPL does not
reach this package and the owner keeps the option of editing the file with anything at all.

Three things this is careful about, all of them because it is money.

**It never guesses at a line it did not understand.** A posting the parser cannot read is counted
and reported, not skipped. A portfolio that silently drops the row it could not parse is worse than
one that refuses to answer, because the number it produces looks complete.

**Every value carries its as-of time and its source.** A quote is "€312.40 from Yahoo, as of Friday
5pm" — not €312.40. Over a weekend that is a two-day-old number and the owner should be told so
rather than having to remember which days markets are open.

**It cannot move anything.** There is no write path, no broker client, and no tool that takes an
amount. 40.F2 asks for that to be asserted rather than merely true, and the gate asserts it against
the whole finance tool family.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from afon.config import settings

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: A quote older than this is called out when it is spoken. A day covers an overnight close; a
#: weekend is two days and the owner is told, which is the point.
STALE_AFTER_S = 24 * 3600.0

#: `  Assets:Broker:VWCE   10.5 VWCE`  — account, amount, commodity. Deliberately narrow: the
#: subset of Beancount that says what is held. Anything else is reported, never assumed.
_POSTING = re.compile(
    r"^\s+(?P<account>[A-Z][A-Za-z0-9:_-]+)\s+(?P<amount>-?[\d,]+(?:\.\d+)?)\s+"
    r"(?P<commodity>[A-Z][A-Z0-9._-]*)\s*(?:;.*)?$")

#: An indented line that is not a posting, a comment, a metadata key or blank. These are what get
#: reported as unread rather than skipped.
_METADATA = re.compile(r"^\s+(?:[a-z][\w-]*:|;)")

#: A posting with an account and no amount — legal Beancount, and the commonest line in a real
#: ledger: it is the balancing leg, "whatever makes this transaction sum to zero". It holds nothing
#: itself, so it is skipped silently. Reporting it as unread would put noise in front of the owner
#: on every single transaction, which is how a real warning stops being read.
_BARE_POSTING = re.compile(r"^\s+[A-Z][A-Za-z0-9:_-]+\s*(?:;.*)?$")


@dataclass
class Ledger:
    """What the file says is held, and what of it could not be read."""

    holdings: dict[str, float] = field(default_factory=dict)   # commodity -> units
    accounts: dict[str, float] = field(default_factory=dict)   # "Assets:Broker:VWCE" -> units
    unread: list[str] = field(default_factory=list)
    path: str = ""
    modified: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def ledger_path() -> Path:
    configured = getattr(settings, "ledger_path", "")
    return Path(configured) if configured else _REPO_ROOT / "ledger.beancount"


def read_ledger(path: Path | None = None) -> Ledger:
    """Sum the postings by commodity. Never raises; an unreadable ledger is an answer."""
    p = Path(path) if path else ledger_path()
    out = Ledger(path=str(p))
    try:
        text = p.read_text(encoding="utf-8")
        out.modified = p.stat().st_mtime
    except OSError as e:
        out.error = f"no ledger at {p} ({type(e).__name__})"
        return out
    for raw in text.splitlines():
        if not raw.strip() or not raw[:1].isspace():
            continue                                  # a directive line, not a posting
        m = _POSTING.match(raw)
        if not m:
            if not _METADATA.match(raw) and not _BARE_POSTING.match(raw):
                out.unread.append(raw.strip()[:120])
            continue
        try:
            units = float(m.group("amount").replace(",", ""))
        except ValueError:
            out.unread.append(raw.strip()[:120])
            continue
        account, commodity = m.group("account"), m.group("commodity")
        if not account.startswith(("Assets", "Liabilities")):
            continue                                  # income and expenses are flow, not holdings
        out.accounts[f"{account} {commodity}"] = out.accounts.get(
            f"{account} {commodity}", 0.0) + units
        out.holdings[commodity] = out.holdings.get(commodity, 0.0) + units
    out.holdings = {k: round(v, 8) for k, v in out.holdings.items() if round(v, 8) != 0}
    return out


# --- valuing it ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Valued:
    """One holding, priced — with where the price came from and when it was true."""

    commodity: str
    units: float
    price: float | None = None
    currency: str = ""
    source: str = ""
    as_of: float = 0.0
    why_not: str = ""

    @property
    def value(self) -> float | None:
        return None if self.price is None else round(self.units * self.price, 2)

    def stale_by(self, now: float | None = None) -> float:
        if not self.as_of:
            return 0.0
        return max(0.0, (now if now is not None else time.time()) - self.as_of)

    def said(self, now: float | None = None) -> str:
        if self.price is None:
            return f"{self.units:g} {self.commodity} — no price ({self.why_not or 'unavailable'})"
        age = self.stale_by(now)
        when = time.strftime("%a %H:%M", time.localtime(self.as_of)) if self.as_of else "unknown"
        line = (f"{self.units:g} {self.commodity} at {self.price:,.2f} {self.currency} "
                f"= {self.value:,.2f} {self.currency} ({self.source}, as of {when}")
        if age > STALE_AFTER_S:
            line += f" — {age / 86400:.0f} days old"
        return line + ")"


@dataclass
class Snapshot:
    holdings: list[Valued] = field(default_factory=list)
    unread: list[str] = field(default_factory=list)
    ledger_modified: float = 0.0
    error: str = ""

    def totals(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for h in self.holdings:
            if h.value is not None:
                out[h.currency] = round(out.get(h.currency, 0.0) + h.value, 2)
        return out

    def spoken(self, now: float | None = None) -> str:
        if self.error:
            return f"I can't value your portfolio, sir — {self.error}."
        if not self.holdings:
            return "Your ledger has no holdings in it, sir."
        lines = [h.said(now) for h in self.holdings]
        said = "Portfolio, sir:\n" + "\n".join(f"- {ln}" for ln in lines)
        totals = self.totals()
        if totals:
            said += "\nTotal: " + ", ".join(f"{v:,.2f} {c}" for c, v in sorted(totals.items()))
        missing = [h.commodity for h in self.holdings if h.price is None]
        if missing:
            said += (f"\nThat total leaves out {', '.join(missing)} — I couldn't price "
                     f"{'it' if len(missing) == 1 else 'them'}, so {'it is' if len(missing) == 1 else 'they are'} "
                     "not in the figure.")
        if self.unread:
            said += (f"\nI couldn't read {len(self.unread)} line(s) in the ledger, so this may be "
                     f"incomplete: {self.unread[0]}")
        if self.ledger_modified:
            days = (time.time() - self.ledger_modified) / 86400
            if days > 30:
                said += f"\nThe ledger itself hasn't been touched in {days:.0f} days."
        return said


#: Commodities that are money rather than an instrument: held at face value, not quoted.
CASH = {"EUR", "USD", "GBP", "CHF", "AMD", "RUB", "JPY"}


async def snapshot(path: Path | None = None, quote=None) -> Snapshot:
    """Value every holding. `quote(commodity) -> (price, currency, as_of, source)` for testing."""
    led = read_ledger(path)
    if not led.ok:
        return Snapshot(error=led.error)
    out = Snapshot(unread=led.unread, ledger_modified=led.modified)
    getter = quote or _quote
    for commodity, units in sorted(led.holdings.items()):
        if commodity in CASH:
            out.holdings.append(Valued(commodity, units, price=1.0, currency=commodity,
                                       source="the ledger", as_of=led.modified))
            continue
        try:
            price, currency, as_of, source = await getter(commodity)
        except Exception as e:  # noqa: BLE001 — one unpriceable holding must not lose the others
            out.holdings.append(Valued(commodity, units, why_not=f"{type(e).__name__}"))
            continue
        if price is None:
            out.holdings.append(Valued(commodity, units, why_not=source or "no quote"))
            continue
        out.holdings.append(Valued(commodity, units, price=price, currency=currency,
                                   source=source, as_of=as_of))
    return out


async def _quote(commodity: str) -> tuple[float | None, str, float, str]:
    """A live quote with the time it was true. Uses the same endpoint `stock_price` already does."""
    from afon.brain.tools.utility import quote_ticker

    return await quote_ticker(commodity)


def _selfcheck() -> None:
    """ponytail: the one runnable check — a ledger that parses, and a line that does not."""
    import asyncio
    import tempfile

    async def fake(commodity):
        return (100.0, "EUR", time.time() - 3 * 86400, "a test")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        p = Path(d) / "ledger.beancount"
        p.write_text(
            '2026-09-01 * "Broker" "Buy"\n'
            "  Assets:Broker:VWCE   10 VWCE\n"
            "  Assets:Broker:Cash  -1,000.00 EUR\n"
            '2026-09-02 * "Broker" "Buy more"\n'
            "  Assets:Broker:VWCE   5 VWCE\n"
            "  Assets:Broker:Cash  -500.00 EUR\n"
            "  this line is nonsense\n"
            "  Expenses:Fees  2.00 EUR\n", encoding="utf-8")
        led = read_ledger(p)
        assert led.holdings["VWCE"] == 15, led.holdings
        assert led.holdings["EUR"] == -1500.0, led.holdings
        assert "Expenses" not in str(led.accounts), led.accounts
        assert led.unread == ["this line is nonsense"], led.unread

        snap = asyncio.run(snapshot(p, quote=fake))
        vwce = next(h for h in snap.holdings if h.commodity == "VWCE")
        assert vwce.value == 1500.0, vwce
        assert "days old" in vwce.said(), vwce.said()
        assert "couldn't read 1 line" in snap.spoken(), snap.spoken()
        assert "no ledger at" in asyncio.run(snapshot(Path(d) / "nope")).spoken()
    print("portfolio self-check ok")


if __name__ == "__main__":
    _selfcheck()
