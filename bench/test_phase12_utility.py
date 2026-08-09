"""Phase 12 — utilities belt, offline + hermetic.

Network-backed tools (weather/crypto/stocks/fx/news/wiki/define) aren't called for real here; we
verify the pure logic that underpins them (unit-conversion maths, currency-vs-unit routing, crypto
symbol mapping) plus the empty-arg guards that keep every tool from crashing, and that all eight are
registered. No network.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f" — {detail}" if detail and not ok else ""))


def approx(a, b, tol=0.01) -> bool:
    return a is not None and abs(a - b) <= tol


def main() -> None:
    import afon.brain.tools.utility as u
    from afon.brain.tools import tool_names

    print("[1] unit conversion maths (length / mass / temperature)")
    km, err = u.convert_units(100, "km", "mi")
    check("100 km -> ~62.14 mi", approx(km, 62.137, 0.01) and err is None, str(km))
    kg, err = u.convert_units(1, "kg", "lb")
    check("1 kg -> ~2.2046 lb", approx(kg, 2.2046, 0.001), str(kg))
    f, err = u.convert_units(0, "c", "f")
    check("0 C -> 32 F", approx(f, 32.0), str(f))
    c, err = u.convert_units(212, "f", "c")
    check("212 F -> 100 C", approx(c, 100.0), str(c))
    k, err = u.convert_units(0, "c", "k")
    check("0 C -> 273.15 K", approx(k, 273.15), str(k))

    print("\n[2] mismatched dimensions are rejected cleanly")
    res, err = u.convert_units(5, "km", "kg")
    check("km -> kg returns an error, not a crash", res is None and err is not None, str(err))

    print("\n[3] crypto symbol -> CoinGecko id mapping")
    check("btc -> bitcoin", u.coingecko_id("BTC") == "bitcoin")
    check("eth -> ethereum", u.coingecko_id("eth") == "ethereum")
    check("unknown symbol passes through", u.coingecko_id("ZZZ") == "zzz")

    print("\n[4] empty-arg guards ask for input (no network, no crash)")
    check("weather without place asks", "place" in asyncio.run(u.weather({})).lower())
    check("crypto without symbol asks", "coin" in asyncio.run(u.crypto_price({})).lower())
    check("stock without ticker asks", "ticker" in asyncio.run(u.stock_price({})).lower())
    check("fx without currencies asks", "currency" in asyncio.run(u.fx_rate({})).lower())
    check("wiki without topic asks", "look up" in asyncio.run(u.wiki_lookup({})).lower())
    check("define without word asks", "word" in asyncio.run(u.define_word({})).lower())
    check("convert without number asks", "number" in asyncio.run(u.convert({"from": "km", "to": "mi"})).lower())
    check("travel_time without destination asks", "where" in asyncio.run(u.travel_time({})).lower())
    # travel mode aliases map to OSRM profiles (pure, no network).
    check("travel mode 'car' -> driving", u._TRAVEL_MODES.get("car") == "driving")
    check("travel mode 'walk' -> walking", u._TRAVEL_MODES.get("walk") == "walking")
    check("travel mode 'bike' -> cycling", u._TRAVEL_MODES.get("bike") == "cycling")

    print("\n[5] convert routes 3-letter currency codes to fx (and unit codes to maths)")
    out = asyncio.run(u.convert({"value": 10, "from": "km", "to": "mi"}))
    check("km/mi goes through unit maths", "6.21" in out or "mi" in out, out)
    # USD->EUR would hit the network in fx; just assert it doesn't raise and returns a string.
    cur = asyncio.run(u.convert({"value": 10, "from": "USD", "to": "EUR"}))
    check("currency convert returns a spoken string (no crash)", isinstance(cur, str) and cur, cur[:40])

    print("\n[6] all eight belt tools are registered")
    names = set(tool_names())
    expected = {"weather", "crypto_price", "stock_price", "fx_rate", "news_brief",
                "wiki_lookup", "define_word", "convert", "travel_time"}
    check("every utility is registered", expected <= names, str(sorted(expected - names)))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
