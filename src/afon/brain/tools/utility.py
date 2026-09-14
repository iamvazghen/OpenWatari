"""Utilities belt — small, high-frequency, mostly no-key lookups (Phase 12).

The owner asked for "minor tools — weather, news, economy, stock prices, crypto prices, and the like":
quick one-shot answers Afon should serve himself without troubling the fleet. The belt favours
**no-key, EU-friendly** providers so most of it works out of the box:

  * weather   — Open-Meteo (geocode + forecast), no key
  * crypto    — CoinGecko simple price, no key
  * stocks    — Stooq CSV quote, no key
  * fx        — Frankfurter (ECB reference rates), no key
  * news      — Hacker News / Algolia search, no key
  * wiki      — Wikipedia REST summary, no key
  * define    — dictionaryapi.dev, no key
  * convert   — built-in unit maths; currency conversions defer to fx

Every call is wrapped in the L4 cache (TTL tuned per volatility) so a repeat is instant, and every
handler self-degrades to a spoken note on any error. Pure helpers (unit maths, symbol mapping) are
factored out so they're unit-tested with no network.
"""

from __future__ import annotations

from afon.brain.cache import CACHE
from afon.brain.tools.base import clip, http_get, missing_arg, tool_error

# ---- pure helpers (offline-testable) ----------------------------------------------------

# Common crypto tickers → CoinGecko ids. Unknown symbols fall back to the symbol itself.
_COINGECKO_IDS = {
    "btc": "bitcoin", "eth": "ethereum", "usdt": "tether", "usdc": "usd-coin",
    "bnb": "binancecoin", "sol": "solana", "xrp": "ripple", "ada": "cardano",
    "doge": "dogecoin", "dot": "polkadot", "matic": "matic-network", "ltc": "litecoin",
    "trx": "tron", "avax": "avalanche-2", "link": "chainlink", "ton": "the-open-network",
}

# Unit conversion: factor to a canonical base per dimension, plus temperature special-casing.
_LENGTH = {"mm": 0.001, "cm": 0.01, "m": 1.0, "km": 1000.0, "in": 0.0254, "inch": 0.0254,
           "ft": 0.3048, "foot": 0.3048, "feet": 0.3048, "yd": 0.9144, "mi": 1609.344,
           "mile": 1609.344, "miles": 1609.344}
_MASS = {"mg": 0.001, "g": 1.0, "kg": 1000.0, "lb": 453.59237, "lbs": 453.59237,
         "oz": 28.349523125, "st": 6350.29318, "ton": 1_000_000.0, "tonne": 1_000_000.0}
_VOLUME = {"ml": 0.001, "l": 1.0, "litre": 1.0, "liter": 1.0, "gal": 3.785411784,
           "gallon": 3.785411784, "pt": 0.473176473, "cup": 0.2365882365, "qt": 0.946352946}
_DIMENSIONS = {"length": _LENGTH, "mass": _MASS, "volume": _VOLUME}


def coingecko_id(symbol: str) -> str:
    s = (symbol or "").strip().lower()
    return _COINGECKO_IDS.get(s, s)


def _to_celsius(value: float, unit: str) -> float | None:
    u = unit.lower().lstrip("°")
    if u in ("c", "celsius"):
        return value
    if u in ("f", "fahrenheit"):
        return (value - 32) * 5 / 9
    if u in ("k", "kelvin"):
        return value - 273.15
    return None


def _from_celsius(value: float, unit: str) -> float | None:
    u = unit.lower().lstrip("°")
    if u in ("c", "celsius"):
        return value
    if u in ("f", "fahrenheit"):
        return value * 9 / 5 + 32
    if u in ("k", "kelvin"):
        return value + 273.15
    return None


def convert_units(value: float, frm: str, to: str) -> tuple[float | None, str | None]:
    """Convert between units of the same dimension. Returns (result, error). Currency is NOT here."""
    frm = (frm or "").strip().lower()
    to = (to or "").strip().lower()
    # Temperature is affine, not a simple ratio.
    temp_units = {"c", "f", "k", "celsius", "fahrenheit", "kelvin", "°c", "°f", "°k"}
    if frm.lstrip("°") in {u.lstrip("°") for u in temp_units} and to.lstrip("°") in {u.lstrip("°") for u in temp_units}:
        c = _to_celsius(value, frm)
        if c is None:
            return None, f"I don't know the temperature unit '{frm}', sir."
        out = _from_celsius(c, to)
        if out is None:
            return None, f"I don't know the temperature unit '{to}', sir."
        return out, None
    for table in _DIMENSIONS.values():
        if frm in table and to in table:
            return value * table[frm] / table[to], None
    return None, f"I can't convert {frm} to {to}, sir — they're different kinds of measure."


# ---- network-backed tools (cached, self-degrading) --------------------------------------

# WMO weather codes, collapsed to what a person would actually say out loud. Ranges rather than
# all 28 codes: "light drizzle" vs "moderate drizzle" is not a distinction worth speaking.
_WMO = [
    (0, 0, "clear"), (1, 2, "partly cloudy"), (3, 3, "overcast"), (45, 48, "foggy"),
    (51, 57, "drizzly"), (61, 67, "rainy"), (71, 77, "snowy"), (80, 82, "showery"),
    (85, 86, "snow showers"), (95, 99, "thunderstorms"),
]


def _wmo(code) -> str:
    try:
        c = int(code)
    except (TypeError, ValueError):
        return ""
    for lo, hi, word in _WMO:
        if lo <= c <= hi:
            return word
    return ""


def _when_mode(raw: str) -> str:
    """'current' | 'tomorrow' | 'week'. Free text, because the model writes what the owner said."""
    w = (raw or "").strip().lower()
    if not w or w in ("now", "today", "current", "currently", "right now", "this morning"):
        return "current"
    if "tomorrow" in w or w in ("next day", "morgen"):
        return "tomorrow"
    if "week" in w or "coming days" in w or "next few days" in w:
        return "week"
    return "current"


async def weather(args: dict) -> str:
    location = (args.get("location") or "").strip()
    if not location:
        return "Which place's weather, sir?"
    # H2.11: this tool used to request ONLY `current=`, so "what's the weather in Berlin tomorrow"
    # was answered with today's temperature — confidently and wrongly. Steering the model away in
    # the description did not work (it still picked `weather`, and `forecast` is in
    # _READ_INTENT_PATTERNS so a tool call is forced anyway). The same open-meteo endpoint already
    # serves `daily=`, so the fix is to support the question rather than deflect it.
    mode = _when_mode(args.get("when") or "")

    async def fetch() -> str:
        geo = await http_get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params={"name": location, "count": 1},
        )
        results = geo.json().get("results") or []
        if not results:
            return f"I couldn't find a place called '{location}', sir."
        g = results[0]
        params = {"latitude": g["latitude"], "longitude": g["longitude"], "timezone": "auto"}
        if mode == "current":
            params["current"] = "temperature_2m,apparent_temperature,wind_speed_10m,weather_code"
        else:
            params["daily"] = ("temperature_2m_max,temperature_2m_min,"
                               "precipitation_probability_max,weather_code")
            params["forecast_days"] = 7 if mode == "week" else 2
        fc = await http_get("https://api.open-meteo.com/v1/forecast", params=params)
        data = fc.json()
        name = g.get("name", location)
        where = f"{name}{', ' + g['country'] if g.get('country') else ''}"

        if mode == "current":
            cur = data.get("current", {})
            sky = _wmo(cur.get("weather_code"))
            return (f"In {where} it's {cur.get('temperature_2m')}°C "
                    f"(feels like {cur.get('apparent_temperature')}°C)"
                    f"{', ' + sky if sky else ''}, wind {cur.get('wind_speed_10m')} km/h, sir.")

        daily = data.get("daily", {})
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        rain = daily.get("precipitation_probability_max") or []
        codes = daily.get("weather_code") or []
        if not highs:
            return f"I couldn't get a forecast for {where}, sir."

        def day(i: int) -> str:
            sky = _wmo(codes[i]) if i < len(codes) else ""
            wet = f", {rain[i]}% chance of rain" if i < len(rain) and rain[i] is not None else ""
            return (f"{highs[i]}°C high, {lows[i]}°C low" if i < len(lows) else f"{highs[i]}°C") \
                + (f", {sky}" if sky else "") + wet

        if mode == "tomorrow":
            if len(highs) < 2:
                return f"I couldn't get tomorrow's forecast for {where}, sir."
            return f"Tomorrow in {where}: {day(1)}, sir."
        # A week spoken in full is unlistenable — give the span and the wettest day.
        span_hi, span_lo = max(highs), min(lows or highs)
        wettest = max(range(len(rain)), key=lambda i: rain[i] or 0) if rain else None
        tail = ""
        if wettest is not None and (rain[wettest] or 0) >= 40:
            names = ["today", "tomorrow"] + [f"in {n} days" for n in range(2, 7)]
            tail = (f" Wettest looks like {names[wettest]} at {rain[wettest]}%.")
        return (f"Over the next {len(highs)} days in {where}: highs up to {span_hi}°C, "
                f"lows around {span_lo}°C, sir.{tail}")

    try:
        # The cache key MUST carry the mode, or "tomorrow" would be served today's cached answer
        # for 15 minutes — reintroducing the exact bug this fixes.
        return await CACHE.cached("weather", f"{location.lower()}|{mode}", ttl=900, factory=fetch)
    except Exception as e:  # noqa: BLE001
        return tool_error("weather", e)


async def crypto_price(args: dict) -> str:
    symbol = (args.get("symbol") or "").strip()
    if not symbol:
        return "Which coin, sir?"
    vs = (args.get("vs") or "usd").strip().lower()
    cid = coingecko_id(symbol)

    async def fetch() -> str:
        r = await http_get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": cid, "vs_currencies": vs, "include_24hr_change": "true"},
        )
        data = r.json().get(cid)
        if not data:
            return f"I couldn't find a price for '{symbol}', sir."
        price = data.get(vs)
        change = data.get(f"{vs}_24h_change")
        chg = f", {change:+.1f}% in 24h" if isinstance(change, (int, float)) else ""
        return f"{symbol.upper()} is {price:,} {vs.upper()}{chg}, sir."

    try:
        return await CACHE.cached("crypto", f"{cid}:{vs}", ttl=120, factory=fetch)
    except Exception as e:  # noqa: BLE001
        return tool_error("crypto price", e)


async def _yahoo_meta(ticker: str) -> dict:
    """One fetch, one owner. Yahoo's public chart endpoint — no key; non-US tickers carry a
    suffix (AIR.DE). Both the spoken price and the portfolio's numeric quote read this."""
    r = await http_get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
        params={"interval": "1d", "range": "1d"},
    )
    results = (((r.json() or {}).get("chart") or {}).get("result")) or []
    return (results[0].get("meta") or {}) if results else {}


async def quote_ticker(symbol: str) -> tuple[float | None, str, float, str]:
    """(price, currency, as-of epoch, source) for one ticker — the numeric half of `stock_price`.

    Split out for the portfolio (40.F3), which needs the NUMBER and, more importantly, the time the
    number was true: a quote read on a Sunday is Friday's close, and a valuation that does not say
    so invites the owner to act on a two-day-old price. Yahoo returns that time and the prose
    version discarded it along with everything else that was not a sentence.
    """
    ticker = (symbol or "").strip().upper()
    if not ticker:
        return None, "", 0.0, "no ticker given"
    meta = await _yahoo_meta(ticker)
    price = meta.get("regularMarketPrice")
    if price is None:
        return None, "", 0.0, f"no quote for {ticker}"
    as_of = meta.get("regularMarketTime") or 0
    return (float(price), str(meta.get("currency") or ""),
            float(as_of) if isinstance(as_of, (int, float)) else 0.0, "Yahoo Finance")


async def stock_price(args: dict) -> str:
    symbol = (args.get("symbol") or "").strip()
    if not symbol:
        return "Which ticker, sir?"
    ticker = symbol.upper()

    async def fetch() -> str:
        meta = await _yahoo_meta(ticker)
        price = meta.get("regularMarketPrice")
        if price is None:
            return f"I couldn't get a quote for '{symbol}', sir."
        cur = meta.get("currency", "")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        chg = ""
        if isinstance(prev, (int, float)) and prev:
            pct = (price - prev) / prev * 100
            chg = f", {pct:+.1f}% on the day"
        return f"{ticker} is trading at {price:,} {cur}{chg}, sir."

    try:
        return await CACHE.cached("stock", ticker, ttl=300, factory=fetch)
    except Exception as e:  # noqa: BLE001
        return tool_error("stock price", e)


async def fx_rate(args: dict) -> str:
    base = (args.get("base") or "").strip().upper()
    quote = (args.get("quote") or "").strip().upper()
    if not (base and quote):
        return "From which currency to which, sir? (e.g. USD to EUR)"
    try:
        amount = float(args.get("amount") or 1)
    except (TypeError, ValueError):
        amount = 1.0

    async def fetch() -> str:
        r = await http_get("https://api.frankfurter.app/latest",
                           params={"from": base, "to": quote})
        rate = (r.json().get("rates") or {}).get(quote)
        if rate is None:
            return f"I couldn't get a {base} to {quote} rate, sir."
        return f"{amount:g} {base} is {amount * rate:,.2f} {quote}, sir."

    try:
        return await CACHE.cached("fx", f"{base}:{quote}:{amount}", ttl=3600, factory=fetch)
    except Exception as e:  # noqa: BLE001
        return tool_error("exchange rate", e)


async def news_brief(args: dict) -> str:
    topic = (args.get("topic") or "").strip()

    async def fetch() -> str:
        if topic:
            r = await http_get("https://hn.algolia.com/api/v1/search",
                               params={"query": topic, "tags": "story", "hitsPerPage": 5})
            hits = r.json().get("hits") or []
            titles = [h.get("title") for h in hits if h.get("title")]
        else:
            ids = (await http_get("https://hacker-news.firebaseio.com/v0/topstories.json")).json()[:5]
            titles = []
            for i in ids:
                item = (await http_get(f"https://hacker-news.firebaseio.com/v0/item/{i}.json")).json()
                if item and item.get("title"):
                    titles.append(item["title"])
        if not titles:
            return f"No headlines for '{topic}', sir." if topic else "No headlines right now, sir."
        head = f"Top on '{topic}', sir: " if topic else "Today's headlines, sir: "
        return head + "; ".join(clip(t, 100) for t in titles[:5])

    try:
        return await CACHE.cached("news", topic.lower() or "_top", ttl=600, factory=fetch)
    except Exception as e:  # noqa: BLE001
        return tool_error("news", e)


async def wiki_lookup(args: dict) -> str:
    topic = (args.get("topic") or "").strip()
    if not topic:
        return "What should I look up, sir?"

    async def fetch() -> str:
        title = topic.replace(" ", "_")
        # Wikipedia's REST API requires a descriptive User-Agent with a contact, or it 403/429s.
        r = await http_get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}",
            headers={"User-Agent": "AfonAssistant/1.0 (https://github.com/afon-assistant; voice assistant)"},
        )
        extract = r.json().get("extract")
        return clip(extract, 600) if extract else f"I found nothing on '{topic}', sir."

    try:
        return await CACHE.cached("wiki", topic.lower(), ttl=86400, factory=fetch)
    except Exception as e:  # noqa: BLE001
        return tool_error("wiki lookup", e)


async def define_word(args: dict) -> str:
    word = (args.get("word") or "").strip()
    if not word:
        return "Which word, sir?"

    async def fetch() -> str:
        r = await http_get(f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}")
        entries = r.json()
        if not isinstance(entries, list) or not entries:
            return f"I couldn't find a definition for '{word}', sir."
        meanings = entries[0].get("meanings") or []
        defs = []
        for m in meanings[:2]:
            pos = m.get("partOfSpeech", "")
            d = (m.get("definitions") or [{}])[0].get("definition", "")
            if d:
                defs.append(f"({pos}) {d}")
        return f"{word}: " + " ".join(defs) if defs else f"No clear definition for '{word}', sir."

    try:
        return await CACHE.cached("define", word.lower(), ttl=86400, factory=fetch)
    except Exception as e:  # noqa: BLE001
        return tool_error("definition", e)


async def convert(args: dict) -> str:
    try:
        value = float(args.get("value"))
    except (TypeError, ValueError):
        return "Give me a number to convert, sir."
    frm = (args.get("from") or "").strip()
    to = (args.get("to") or "").strip()
    if not (frm and to):
        return "Convert from which unit to which, sir?"
    # Three-letter codes that aren't units → treat as a currency conversion (defer to fx).
    if len(frm) == 3 and len(to) == 3 and frm.lower() not in _LENGTH and frm.lower() not in _MASS:
        return await fx_rate({"base": frm, "quote": to, "amount": value})
    result, err = convert_units(value, frm, to)
    if err:
        return err
    return f"{value:g} {frm} is {result:,.4g} {to}, sir."


async def _geocode_one(name: str) -> dict | None:
    """Resolve a place name to {name, latitude, longitude, country} via Open-Meteo (no key)."""
    try:
        geo = await http_get("https://geocoding-api.open-meteo.com/v1/search",
                             params={"name": name, "count": 1})
        results = geo.json().get("results") or []
        return results[0] if results else None
    except Exception:  # noqa: BLE001
        return None


# OSRM travel profiles (keyless public demo server). Transit/public-transport isn't covered by OSRM;
# for that Afon falls back to the Composio google_maps toolkit via composio_find_tools.
_TRAVEL_MODES = {"drive": "driving", "driving": "driving", "car": "driving",
                 "walk": "walking", "walking": "walking", "foot": "walking",
                 "bike": "cycling", "cycling": "cycling", "cycle": "cycling"}


async def osrm_travel_time(args: dict) -> str:
    """Travel time + distance between two places (driving/walking/cycling). Keyless via OSRM.
    Not registered directly any more: tools/maps.py exposes the single `travel_time` tool and
    delegates HERE when no Google Maps key is configured (Google adds live traffic)."""
    dest = (args.get("to") or args.get("destination") or "").strip()
    if not dest:
        # This, not maps.travel_time, is the branch that runs when no Google Maps key is set --
        # i.e. the default today. Fixing only the Google side would have left the live path broken.
        return missing_arg("travel_time", args, "to", "destination", "from", "origin", "mode",
                           ask="Where to, sir?")
    origin = (args.get("from") or args.get("origin") or "").strip()
    if not origin:
        from afon.brain import prefs
        origin = prefs.home_location() or ""
    if not origin:
        return ("From where, sir? Give me a starting point, or set your home location "
                "(say 'set my home to <place>').")
    mode = _TRAVEL_MODES.get((args.get("mode") or "drive").strip().lower(), "driving")

    async def fetch() -> str:
        o, d = await _geocode_one(origin), await _geocode_one(dest)
        if not o:
            return f"I couldn't find '{origin}', sir."
        if not d:
            return f"I couldn't find '{dest}', sir."
        url = (f"https://router.project-osrm.org/route/v1/{mode}/"
               f"{o['longitude']},{o['latitude']};{d['longitude']},{d['latitude']}")
        r = await http_get(url, params={"overview": "false"})
        data = r.json()
        routes = data.get("routes") or []
        if not routes:
            return (f"I couldn't find a {mode} route from {o.get('name', origin)} to "
                    f"{d.get('name', dest)}, sir.")
        secs = routes[0]["duration"]
        km = routes[0]["distance"] / 1000.0
        mins = round(secs / 60)
        if mins >= 60:
            dur = f"{mins // 60}h {mins % 60}m"
        else:
            dur = f"{mins} minute{'s' if mins != 1 else ''}"
        verb = {"driving": "by car", "walking": "on foot", "cycling": "by bike"}[mode]
        return (f"About {dur} {verb} from {o.get('name', origin)} to {d.get('name', dest)}, sir — "
                f"roughly {km:.0f} km.")

    try:
        return await CACHE.cached("travel", f"{origin}|{dest}|{mode}".lower(), ttl=600, factory=fetch)
    except Exception as e:  # noqa: BLE001
        return tool_error("travel time", e)


SCHEMAS = [
    {"type": "function", "function": {
        "name": "weather",
        "description": "Weather for a place — current conditions, tomorrow, or the week ahead. "
                       "Use for 'what's the weather', 'how cold is it', 'do I need a jacket', "
                       "'will it rain tomorrow', 'what's the week looking like'. Pass `when` "
                       "whenever the owner names a time other than now.",
        "parameters": {"type": "object", "properties": {
            "location": {"type": "string", "description": "City or place name."},
            "when": {"type": "string",
                     "description": "'today' (default), 'tomorrow', or 'this week'."}},
            "required": ["location"]}}},
    {"type": "function", "function": {
        "name": "crypto_price",
        "description": "Live cryptocurrency price by ticker (btc, eth, sol…) with its 24-hour "
                       "change. Use for 'what's bitcoin at', 'how's ether doing'. Coins only — "
                       "for shares use stock_price.",
        "parameters": {"type": "object", "properties": {
            "symbol": {"type": "string", "description": "Coin ticker, e.g. BTC."},
            "vs": {"type": "string", "description": "Quote currency (default usd)."}},
            "required": ["symbol"]}}},
    {"type": "function", "function": {
        "name": "stock_price",
        "description": "Latest stock/ETF quote by ticker, with its move on the day. Use for "
                       "'how's Apple doing', 'what's NVDA at'. US tickers assumed unless a market "
                       "suffix is given (e.g. 'air.de'). Shares only — for coins use crypto_price.",
        "parameters": {"type": "object", "properties": {
            "symbol": {"type": "string", "description": "Ticker symbol."}},
            "required": ["symbol"]}}},
    {"type": "function", "function": {
        "name": "fx_rate",
        "description": "Convert money between two currencies at the latest ECB reference rate. Use "
                       "for 'how much is 500 dollars in euros', 'what's the pound at'. Currency "
                       "only — for units (km, kg, °C) use convert.",
        "parameters": {"type": "object", "properties": {
            "base": {"type": "string", "description": "From currency code, e.g. USD."},
            "quote": {"type": "string", "description": "To currency code, e.g. EUR."},
            "amount": {"type": "number", "description": "Amount to convert (default 1)."}},
            "required": ["base", "quote"]}}},
    {"type": "function", "function": {
        "name": "news_brief",
        # Says Hacker News on purpose: this reads HN's top stories / HN search and nothing else, so
        # the old "top tech/world stories" actively steered the model here for world and local news
        # it cannot answer.
        "description": "Top Hacker News headlines, or HN stories matching a topic. Use for 'what's "
                       "on Hacker News', 'any tech news'. Tech/startup only — for world, local or "
                       "business news use web_search.",
        "parameters": {"type": "object", "properties": {
            "topic": {"type": "string", "description": "Optional topic; blank = top stories."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "wiki_lookup",
        "description": "A short Wikipedia summary of one topic, person or thing. Use for 'who is X', "
                       "'what is Y'. Encyclopaedic facts only — for anything current or news-driven "
                       "use web_search.",
        "parameters": {"type": "object", "properties": {
            "topic": {"type": "string", "description": "What to look up."}},
            "required": ["topic"]}}},
    {"type": "function", "function": {
        "name": "define_word",
        "description": "Dictionary definition of a single ENGLISH word, with its part of speech. "
                       "Use for 'what does X mean', 'define X'. One word only — for a concept, "
                       "phrase or foreign word use wiki_lookup or web_search.",
        "parameters": {"type": "object", "properties": {
            "word": {"type": "string", "description": "The word to define."}},
            "required": ["word"]}}},
    {"type": "function", "function": {
        "name": "convert",
        "description": "Convert a quantity between units of length, mass, volume or temperature. "
                       "Use for 'how many miles is 10 km', '200 grams in ounces'. Three-letter "
                       "currency codes are handed to fx_rate automatically.",
        "parameters": {"type": "object", "properties": {
            "value": {"type": "number", "description": "The number to convert."},
            "from": {"type": "string", "description": "Source unit or 3-letter currency code."},
            "to": {"type": "string", "description": "Target unit or 3-letter currency code."}},
            "required": ["value", "from", "to"]}}},
]

# Back-compat alias (bench/test_phase12_utility drives the OSRM path by this name).
travel_time = osrm_travel_time

HANDLERS = {
    "weather": weather,
    "crypto_price": crypto_price,
    "stock_price": stock_price,
    "fx_rate": fx_rate,
    "news_brief": news_brief,
    "wiki_lookup": wiki_lookup,
    "define_word": define_word,
    "convert": convert,
}
