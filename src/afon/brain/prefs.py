"""Runtime preferences Afon can change on the fly, persisted to JSON.

Some things shouldn't be frozen in ``.env``. The owner's **home location** is the clearest case: it's
Cologne most of the year, but could be Armenia or France for a whole summer. So it's a *variable*,
not a constant — Afon updates it at runtime (via the ``set_home_location`` tool), it persists
across restarts in ``runtime_prefs.json``, and it overrides the ``.env`` default. Anything unset
falls back to ``settings``.
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from afon.config import settings
from afon.shared.paths import state_dir


def _path() -> Path:
    return state_dir() / "runtime_prefs.json"


def _load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8")) if _path().is_file() else {}
    except Exception:  # noqa: BLE001 — a corrupt/locked file must never break a turn
        return {}


def get(key: str, default=None):
    return _load().get(key, default)


def set(key: str, value) -> None:  # noqa: A001 — small, intentional get/set API
    data = _load()
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    try:
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info(f"pref set: {key}={value!r}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"could not persist pref '{key}': {e}")


def home_location() -> str | None:
    """The owner's CURRENT home — the runtime override if set, else the ``.env`` default."""
    return get("home_location") or settings.home_location
