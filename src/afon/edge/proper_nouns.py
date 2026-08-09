"""Phase 1.5 — proper-noun biasing for STT.

"Yerevan" became "your event" on every engine because the recogniser has no idea those words matter
to this owner. The fix is to hand the STT a small vocabulary of the names/places/projects it should
favour. This module builds that list — automatically from who the assistant is (its own name, the
owner's name + form of address) and the owner's contacts, plus anything configured in
``AFON_STT_HOTWORDS`` — and the STT builders pass it to the engine (Whisper ``hotwords=``, Deepgram
nova-3 ``keyterm=``). Biasing at the source beats guessing after the fact, so there's no risky
post-hoc fuzzy correction here.
"""

from __future__ import annotations

from functools import lru_cache

from loguru import logger


def _split(csv: str) -> list[str]:
    return [t.strip() for t in (csv or "").replace(";", ",").split(",") if t.strip()]


def hotwords_list(max_terms: int = 100) -> list[str]:
    """Assemble the proper-noun bias list: configured terms + identity + contact names.

    De-duplicated (case-insensitive, first spelling wins), capped so a huge contact book can't bloat
    the request. Never raises — a missing contacts file / config just yields fewer terms.
    """
    from afon.config import settings

    terms: list[str] = []
    terms += _split(settings.stt_hotwords)
    for val in (settings.assistant_name, settings.user_name, settings.user_address):
        v = (val or "").strip()
        if v:
            terms.append(v)
    try:
        from afon.brain.contacts import BOOK

        for c in BOOK.all():
            if c.name:
                terms.append(c.name.strip())
    except Exception as e:  # noqa: BLE001 — contacts are optional; biasing still works without them
        logger.debug(f"hotwords: contacts unavailable ({type(e).__name__})")

    seen: set[str] = set()
    out: list[str] = []
    for t in terms:
        key = t.lower()
        if t and key not in seen:
            seen.add(key)
            out.append(t)
        if len(out) >= max_terms:
            break
    return out


@lru_cache(maxsize=1)
def hotwords_str() -> str:
    """The bias list as a single space-joined string (Whisper's ``hotwords=`` wants one string)."""
    return " ".join(hotwords_list())
