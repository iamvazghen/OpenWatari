"""The one place that opens a memory database (SYSTEMS.md 30.F2).

Five stores — coaching, graph, presence, semantic, tasks — each held their own `sqlite3.connect`,
each with slightly different arguments. Three passed no `timeout` at all, which means the sqlite
default of 5 seconds applied by accident rather than by decision, and two set `row_factory` while
the others did not. None of that was written down anywhere; it was five independent guesses that
happened to coexist.

That matters more than tidiness. A busy-timeout is the difference between a slow write and
`database is locked` on a brain that writes from a scheduler thread and an HTTP handler at the
same time, and a setting nobody chose is a setting nobody can change: raising it meant finding
five call sites and hoping there was not a sixth. There was very nearly a sixth every time a store
was added.

So the connection arguments live here, once, and `test_layering.py` fails the build if a module
outside this one opens a memory database directly.

    from afon.brain.dbconn import connect
    with connect(self._path) as c: ...

ponytail: deliberately NOT enabling WAL. It is the obvious next pragma and it would help
concurrent reads, but it splits a store into `.sqlite` + `-wal` + `-shm`, and the backup drill
(22.F4) archives files — a copy taken mid-transaction would restore, verify, and be missing the
newest writes. Worth doing when the backup learns to checkpoint first; not worth doing quietly.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: How long to wait for another writer before raising `database is locked`. The brain writes from
#: the scheduler thread, the HTTP handler and the turn path, so contention is normal, not a bug.
DEFAULT_TIMEOUT_S = 5.0


#: How many connections this process has opened and not yet closed. Exposed so a test can assert
#: the count returns to zero, which is the only way to tell a closed connection from one that
#: CPython's refcounting happens to have collected for you — the distinction that matters on a
#: brain that runs for weeks.
OPEN = 0


class _ClosingConnection(sqlite3.Connection):
    """A connection that actually closes when its ``with`` block ends.

    `with sqlite3.connect(...) as c` commits or rolls back — and does **not** close. That is a
    documented sqlite3 surprise and it is easy to read straight past, because the code looks
    exactly like a file handle that does close. All five stores open a fresh connection per
    operation inside a `with`, so every read and write was leaking one, for the life of a brain
    that runs for weeks.

    Found by a test's teardown: on Windows the leaked handle keeps a lock on the file and
    `TemporaryDirectory` cleanup raises. On Linux it fails silently upward into the fd limit,
    which is the same bug with a slower and less obvious ending.
    """

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()

    def close(self) -> None:
        global OPEN
        if not getattr(self, "_afon_closed", False):
            self._afon_closed = True
            OPEN -= 1
        super().close()


def connect(path: str | Path, *, timeout: float = DEFAULT_TIMEOUT_S,
            rows: bool = True) -> sqlite3.Connection:
    """Open a memory database with this brain's agreed settings.

    `rows=True` gives `sqlite3.Row` (index *and* name access), which is what a caller wants unless
    it is reading single scalar columns. Use it in a `with` block: the connection closes at the
    end of one, and only there.
    """
    global OPEN
    conn = sqlite3.connect(str(path), timeout=timeout, factory=_ClosingConnection)
    OPEN += 1
    if rows:
        conn.row_factory = sqlite3.Row
    return conn
