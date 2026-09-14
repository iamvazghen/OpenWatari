"""40.F1-F3 — read the owner's ledger, value it honestly, and move nothing.

  [ledger]     a Beancount plain-text file the owner controls, summed by commodity, with anything
               the parser could not read REPORTED rather than skipped — a portfolio that silently
               drops the line it did not understand produces a number that looks complete;
  [no mutating tool] asserted against the whole finance family, not merely absent today. No buy,
               no sell, no transfer, no broker credential, and no parameter that takes an amount;
  [staleness]  every value carries where the price came from and when it was true, and a quote
               older than a day says so out loud.

Hermetic: a temporary ledger and a stub quote. No network.

    uv run python bench/test_portfolio.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


LEDGER = '''2026-01-01 open Assets:Broker:VWCE  VWCE

2026-03-14 * "Broker" "Buy"
  Assets:Broker:VWCE        12 VWCE
  Assets:Broker:Cash  -1,440.00 EUR

2026-05-02 * "Broker" "Top up"
  Assets:Broker:VWCE         8 VWCE
  Assets:Broker:Cash    -980.00 EUR

2026-06-20 * "Transfer in"
  Assets:Broker:Cash   3,000.00 EUR
  Equity:Opening-Balances

2026-06-21 * "Fees"
  Expenses:Fees           12.00 EUR
  Assets:Broker:Cash     -12.00 EUR

2026-07-01 * "Wallet"
  Assets:Wallet:BTC        0.25 BTC
  Assets:Broker:Cash  -15,000.00 EUR
'''


def main() -> None:
    from afon.brain import portfolio as P

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
        led_path = Path(d) / "ledger.beancount"
        led_path.write_text(LEDGER, encoding="utf-8")

        print("[ledger] 40.F1 — plain text the owner controls, summed by commodity")
        led = P.read_ledger(led_path)
        check("the ledger reads", led.ok, led.error)
        check("shares add up across transactions", led.holdings.get("VWCE") == 20, led.holdings)
        check("cash nets out", led.holdings.get("EUR") == -14432.0, led.holdings)
        check("a second commodity is kept apart", led.holdings.get("BTC") == 0.25, led.holdings)
        check("thousands separators are read", -14432.0 in led.holdings.values(), led.holdings)
        check("expenses are flow, not holdings",
              not any("Expenses" in k for k in led.accounts), sorted(led.accounts))
        check("...but the cash leg of that same transaction IS counted",
              any("Assets:Broker:Cash" in k for k in led.accounts), sorted(led.accounts))
        check("a balancing leg with no amount is not noise",
              led.unread == [], led.unread)
        check("per-account detail survives, not just totals",
              led.accounts.get("Assets:Broker:VWCE VWCE") == 20, led.accounts)

        print("\n[ledger] a line it cannot read is REPORTED, never skipped")
        messy = Path(d) / "messy.beancount"
        messy.write_text(LEDGER + '\n2026-08-01 * "Odd"\n  Assets:Broker:Cash  about a thousand\n',
                         encoding="utf-8")
        m = P.read_ledger(messy)
        check("the unreadable posting is named", m.unread == ["Assets:Broker:Cash  about a thousand"],
              m.unread)
        check("...and the rest still adds up", m.holdings.get("VWCE") == 20, m.holdings)
        check("a missing ledger is an answer, not a crash",
              not P.read_ledger(Path(d) / "nope.beancount").ok)
        check("...that says where it looked",
              "no ledger at" in P.read_ledger(Path(d) / "nope.beancount").error)

        print("\n[staleness] 40.F3 — as-of and source travel with every value")
        now = time.time()

        async def fresh_quote(commodity):
            return (100.0, "EUR", now - 600, "Yahoo Finance")

        snap = asyncio.run(P.snapshot(led_path, quote=fresh_quote))
        vwce = next(h for h in snap.holdings if h.commodity == "VWCE")
        check("a holding is valued", vwce.value == 2000.0, vwce)
        check("...naming the source", vwce.source == "Yahoo Finance", vwce)
        check("...and when the price was true", vwce.as_of > 0, vwce)
        said = vwce.said(now)
        check("the spoken line carries both", "Yahoo Finance" in said and "as of" in said, said)
        check("a fresh quote is not called old", "days old" not in said, said)
        check("cash is held at face value, sourced to the ledger",
              next(h for h in snap.holdings if h.commodity == "EUR").source == "the ledger")

        async def stale_quote(commodity):
            return (100.0, "EUR", now - 3 * 86400, "Yahoo Finance")

        old = asyncio.run(P.snapshot(led_path, quote=stale_quote))
        v = next(h for h in old.holdings if h.commodity == "VWCE")
        check("a three-day-old quote is flagged", "3 days old" in v.said(now), v.said(now))
        check("...in the spoken snapshot too", "days old" in old.spoken(now), old.spoken(now)[:300])

        print("\n[staleness] what could not be priced is left OUT of the total, and said so")

        async def no_btc(commodity):
            if commodity == "BTC":
                return (None, "", 0.0, "no quote for BTC")
            return (100.0, "EUR", now, "Yahoo Finance")

        gap = asyncio.run(P.snapshot(led_path, quote=no_btc))
        btc = next(h for h in gap.holdings if h.commodity == "BTC")
        check("an unpriceable holding still appears", btc.units == 0.25, btc)
        check("...with no invented value", btc.value is None, btc)
        check("...and the reason", "no quote" in btc.why_not, btc)
        text = gap.spoken(now)
        check("the total says what it leaves out", "leaves out BTC" in text, text)
        check("...and that the figure is therefore partial",
              "not in the figure" in text, text)

        async def explodes(commodity):
            raise RuntimeError("the quote service fell over")

        broken = asyncio.run(P.snapshot(led_path, quote=explodes))
        check("one failing quote does not lose the whole portfolio",
              len(broken.holdings) == 3, broken.holdings)
        check("...and cash is still valued", broken.totals().get("EUR") is not None,
              broken.totals())

        print("\n[ledger] a ledger nobody has touched in months says so")
        import os

        stale_file = Path(d) / "old.beancount"
        stale_file.write_text(LEDGER, encoding="utf-8")
        long_ago = now - 200 * 86400
        os.utime(stale_file, (long_ago, long_ago))
        text = asyncio.run(P.snapshot(stale_file, quote=fresh_quote)).spoken(now)
        check("the age of the ledger itself is reported", "hasn't been touched in 200 days" in text,
              text[-200:])
        check("...and an unread line warns the figure may be incomplete",
              "may be incomplete" in asyncio.run(P.snapshot(messy, quote=fresh_quote)).spoken(now))

    print("\n[no mutating tool] 40.F2 — asserted, not merely absent")
    from afon.brain.tools import tool_handlers, tool_schemas

    schemas = {s["function"]["name"]: s["function"] for s in tool_schemas()}
    FINANCE = {n for n in schemas
               if any(w in n for w in ("portfolio", "price", "ledger", "fx_", "crypto", "stock_",
                                       "invest", "trade", "order", "broker"))}
    check("the finance family is found at all", len(FINANCE) >= 3, sorted(FINANCE))
    MUTATING = ("buy", "sell", "trade", "transfer", "withdraw", "deposit", "send_money", "pay",
                "order", "execute", "move_")
    offenders = sorted(n for n in FINANCE if any(w in n for w in MUTATING))
    check("no finance tool is named for moving money", not offenders, offenders)
    # A DESTINATION is the thing that makes a tool able to move money. An `amount` on its own is
    # not: `fx_rate(amount, base, quote)` converts a number and returns a number, which is
    # arithmetic. Checking for `amount` alone flagged it, and weakening the check to pass would
    # have been the wrong repair — so the check names the property that actually matters.
    DESTINATIONS = ("to_account", "destination", "recipient", "counterparty", "iban", "wallet",
                    "account_to", "payee")
    with_dest = sorted(
        n for n in FINANCE
        if set((schemas[n].get("parameters") or {}).get("properties", {})) & set(DESTINATIONS))
    check("no finance tool takes somewhere to send it", not with_dest, with_dest)
    fx = (schemas.get("fx_rate", {}).get("parameters") or {}).get("properties", {})
    check("the one tool that takes an amount only converts it",
          "amount" not in fx or not (set(fx) & set(DESTINATIONS)), sorted(fx))
    check("the portfolio tool is the only one in its module",
          len(__import__("afon.brain.tools.portfolio", fromlist=["x"]).HANDLERS) == 1)
    check("it is registered", "portfolio_snapshot" in tool_handlers())

    src = (ROOT / "src/afon/brain/portfolio.py").read_text(encoding="utf-8")
    tool_src = (ROOT / "src/afon/brain/tools/portfolio.py").read_text(encoding="utf-8")
    # The self-check at the bottom writes a temporary ledger of its own, so scan the module proper.
    shipped = src.split("def _selfcheck", 1)[0]
    for word in ("write_text", "http_post", "requests.post", "urlopen"):
        check(f"the module has no {word!r} path", word not in shipped, word)
    check("...and never opens the ledger for writing",
          'open(' not in shipped or '"w"' not in shipped and "'w'" not in shipped)
    check("the tool schema says out loud that it cannot move anything",
          "cannot buy, sell or move" in tool_src, tool_src[:200])
    check("Beancount is a format, not an import", "import beancount" not in src.lower())
    check("...and the reason is recorded", "never linked" in src)

    print("\n[no mutating tool] the declined alternatives are recorded with their reason")
    check("bank aggregation is declined in the module, not just the plan",
          "consent surface" in src, "a reader of the code should find the reasoning too")

    print("\n[ledger] the example ships, parses, and is gitignored")
    example = ROOT / "ledger.example.beancount"
    check("the example exists", example.is_file())
    ex = P.read_ledger(example)
    check("...and parses with the real parser", ex.ok and ex.holdings, ex.error or ex.holdings)
    check("...with nothing unread", ex.unread == [], ex.unread)
    check("the real ledger is gitignored",
          "ledger.beancount" in (ROOT / ".gitignore").read_text(encoding="utf-8"),
          "it is his financial position")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
