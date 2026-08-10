"""Pretend it is another day, so date-dependent tests can be caught on purpose.

Imported automatically by CPython at startup when this directory is on PYTHONPATH — which means
it lands BEFORE any test does `from datetime import date`, so the name a test binds is already the
patched one. Set AFON_FAKE_TODAY=YYYY-MM-DD to arm it; unset, this file does nothing at all.

Why it exists: `test_calendar_dates` asserted `"T" not in s` against an all-day string formatted
as "on Tue 11 August" — the T in "Tue". It failed on Tuesdays and Thursdays and passed the other
five days, and was only caught because one run happened to straddle midnight into a Tuesday.
Finding that class of bug by luck does not scale; running the suite on a chosen weekday does.
"""
import os

_fake = os.environ.get("AFON_FAKE_TODAY", "").strip()
if _fake:
    import datetime as _dt

    _y, _m, _d = (int(x) for x in _fake.split("-"))
    _real_date, _real_datetime = _dt.date, _dt.datetime

    class _Date(_real_date):
        @classmethod
        def today(cls):
            return cls(_y, _m, _d)

    class _DateTime(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            # Keep the real wall-clock TIME; only move the calendar day. A test that measures a
            # duration must still see time advance, so this cannot be a frozen instant.
            real = _real_datetime.now(tz)
            return real.replace(year=_y, month=_m, day=_d)

        @classmethod
        def today(cls):
            return cls.now()

        @classmethod
        def utcnow(cls):
            return _real_datetime.utcnow().replace(year=_y, month=_m, day=_d)

    _dt.date = _Date
    _dt.datetime = _DateTime

    # `time.time()` has to move by the SAME amount, or code that mixes the two clocks breaks in a
    # way that looks like a product bug and is not. `presence` stamps rows with time.time() and
    # then computes the day's bounds from datetime.now(): shift only the second and every row
    # falls outside every window, so screen_time reports "no activity recorded for today".
    # Shift by whole days so the wall-clock time of day, and every duration, is untouched.
    import time as _time

    _offset = (_Date(_y, _m, _d) - _real_date.today()).total_seconds()
    if _offset:
        _real_time = _time.time
        _time.time = lambda: _real_time() + _offset
