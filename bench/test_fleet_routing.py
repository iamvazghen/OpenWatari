"""Fleet routing memory — repeated delegated domains surface as a system-prompt bias line."""

from __future__ import annotations

from afon.brain import fleet, prefs


def test_routing_memory(monkeypatch) -> None:
    store: dict = {}
    monkeypatch.setattr(prefs, "get", lambda k, default=None: store.get(k, default))
    monkeypatch.setattr(prefs, "set", lambda k, v: store.__setitem__(k, v))

    assert fleet.routing_hint() == ""  # nothing learned yet
    fleet._note_success("research the ETF market and write a report")
    assert fleet.routing_hint() == ""  # one success isn't a pattern
    fleet._note_success("compare portfolio ETF options")
    hint = fleet.routing_hint()
    assert "finance/markets" in hint and "delegate" in hint

    # A domain never delegated stays out of the hint.
    assert "real estate" not in hint


class _Monkeypatch:
    """Just enough of pytest's fixture to run this file as a script (see J6.5).

    The fixture argument is why this one never ran outside pytest: the registry invoked the module,
    nothing called the function, and exit 0 was read as a pass.
    """

    def __init__(self) -> None:
        self._undo: list[tuple[object, str, object]] = []

    def setattr(self, obj, name, value) -> None:  # noqa: A003
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self) -> None:
        for obj, name, old in reversed(self._undo):
            setattr(obj, name, old)


if __name__ == "__main__":
    mp = _Monkeypatch()
    try:
        test_routing_memory(mp)
    finally:
        mp.undo()
    print("=== 4/4 checks passed ===")
