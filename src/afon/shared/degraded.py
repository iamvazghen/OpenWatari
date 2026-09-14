"""32.F3 — the degraded modes, declared once and read by both hosts.

Model failover was real; nothing above it was. Losing a host produced whatever each component
happened to do on its own — a tool that timed out, a socket that reconnected forever, an answer
that quietly came back without the half of Afon that was gone. None of it was written down, so
there was no way to ask "what works right now" and no moment at which the owner was told.

The plan declined Kubernetes, Consul and etcd, and the reason is the whole design here: consensus
systems coordinate many nodes, and there are two. **At two nodes the honest answer is a declared
mode and a reconciliation rule.** So this is a table.

It lives in `shared/` because both processes need the same one. Two copies of a degraded-mode
matrix is the failure that made the fleet run two identity files, in miniature: they agree on the
day they are written and drift silently after.

Each mode says what is LOST and what is KEPT, in the owner's terms, because "degraded" on its own
tells him nothing he can act on. And each is announced on the way in **and on the way out** — a
recovery nobody mentions leaves him working around a limitation that no longer exists.
"""

from __future__ import annotations

from dataclasses import dataclass

FULL = "full"
BRAIN_DOWN = "brain-down"
EDGE_DOWN = "edge-down"
NETWORK_DOWN = "network-down"


@dataclass(frozen=True)
class Mode:
    """One declared state of the world: what is gone, what still works, and what to say."""

    name: str
    lost: str
    kept: str
    said: str

    def __bool__(self) -> bool:
        return self.name != FULL


#: The matrix. Ordered by severity: the first match wins, so losing the network — which takes the
#: brain with it from the laptop's point of view — is not reported as three separate problems.
MATRIX: dict[str, Mode] = {
    NETWORK_DOWN: Mode(
        NETWORK_DOWN,
        lost="anything that needs the internet: the cloud models, search, email, calendar, Telegram",
        kept="the local model, the microphone, and everything on this machine",
        said="Sir, I've lost the network. I'm on the local model and local tools only — no search, "
             "no mail, no calendar, and nothing I say about the outside world is current."),
    BRAIN_DOWN: Mode(
        BRAIN_DOWN,
        lost="memory, the vault, reminders, and every tool that lives on the brain host",
        kept="listening, speaking, and the reflexes that run on this machine",
        said="Sir, I can't reach my brain host. I can still hear you and answer simple things from "
             "here, but I have no memory, no reminders and no tools until it's back."),
    EDGE_DOWN: Mode(
        EDGE_DOWN,
        lost="voice, the camera, the screen, and control of the laptop",
        kept="text channels, reminders, memory and everything scheduled",
        said="Sir, the laptop side is down, so I can't hear or speak and I can't touch that "
             "machine. Telegram still reaches me, and your reminders still run."),
    FULL: Mode(FULL, lost="nothing", kept="everything", said=""),
}


def current(*, brain_up: bool = True, edge_up: bool = True, network_up: bool = True) -> Mode:
    """Which mode this host is in. Severity order, so one cause is not reported three times."""
    if not network_up:
        return MATRIX[NETWORK_DOWN]
    if not brain_up:
        return MATRIX[BRAIN_DOWN]
    if not edge_up:
        return MATRIX[EDGE_DOWN]
    return MATRIX[FULL]


class Announcer:
    """Says a mode once on entry and once on recovery, and nothing in between.

    The in-between is the part that matters. A degraded mode repeated every turn is noise the owner
    learns to talk over, and a recovery nobody mentions leaves him working around a limitation that
    has been gone for an hour.
    """

    def __init__(self) -> None:
        self.mode: Mode = MATRIX[FULL]

    def update(self, mode: Mode) -> str:
        """Return what to say about this reading, or '' if it changes nothing."""
        if mode.name == self.mode.name:
            return ""
        was, self.mode = self.mode, mode
        if mode.name == FULL:
            return f"Back to normal, sir — {was.lost} is working again."
        return mode.said


def _selfcheck() -> None:
    """ponytail: the one runnable check — severity order, and announce-once."""
    assert current().name == FULL and not current()
    assert current(edge_up=False).name == EDGE_DOWN
    assert current(brain_up=False).name == BRAIN_DOWN
    # A dead network takes the brain with it from the laptop; report the cause, not the symptom.
    assert current(brain_up=False, network_up=False).name == NETWORK_DOWN

    a = Announcer()
    assert a.update(MATRIX[FULL]) == ""
    first = a.update(MATRIX[EDGE_DOWN])
    assert "laptop side is down" in first, first
    assert a.update(MATRIX[EDGE_DOWN]) == "", "a mode announced twice"
    back = a.update(MATRIX[FULL])
    assert back.startswith("Back to normal") and "voice" in back, back
    assert all(m.lost and m.kept for m in MATRIX.values() if m.name != FULL)
    print("degraded-mode self-check ok")


if __name__ == "__main__":
    _selfcheck()
