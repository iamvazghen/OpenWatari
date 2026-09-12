"""One owner of "is something playing, and where" (42.F2).

Audio leaves Afon by two unrelated routes. The desktop player is an ffplay process on the laptop,
reached over the PC link. The music room is a pytgcalls stream into a Telegram group call, which
plays on other people's phones and never touches the laptop's speakers at all.

Each route knew only about itself, and the two tools that answer for them had drifted apart:
``stop_music`` asked the music room before giving up, and ``now_playing`` did not. So with a track
streaming into the room, "what's playing?" answered "Nothing is playing out loud right now, sir."
That is the exact failure S42's elite bar names — a confident wrong answer about something the owner
can hear — and it survived because there was no single place that knew.

This is that place. Both tools ask it, and a new one can ask it without learning the routes.

    uv run python -m afon.brain.playback     # self-check
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

#: Where the audio is going. The distinction matters beyond bookkeeping: DESKTOP plays through the
#: laptop's own output, so the laptop's volume is the volume. ROOM plays into a Telegram call, where
#: how loud it is belongs to whoever is listening — a fact Afon has to say rather than pretend away.
DESKTOP = "desktop"
ROOM = "music room"


@dataclass(frozen=True)
class Playing:
    where: str = ""     # DESKTOP | ROOM | "" when nothing is playing
    label: str = ""

    def __bool__(self) -> bool:
        return bool(self.where)


async def current() -> Playing:
    """What is playing and where, asking every route. ``Playing()`` when nothing is."""
    try:
        from afon.brain.tools.localplay import desktop_now_playing

        label = await desktop_now_playing()
        if label:
            return Playing(DESKTOP, label)
    except Exception as e:  # noqa: BLE001 — an unreachable laptop is not "nothing is playing"
        logger.debug(f"playback: desktop unreadable ({type(e).__name__}: {e})")
    try:
        from afon.brain.tools import voicechat

        label = voicechat.room_is_playing()
        if label:
            return Playing(ROOM, str(label))
    except Exception as e:  # noqa: BLE001 — pytgcalls absent is the normal case
        logger.debug(f"playback: music room unreadable ({type(e).__name__}: {e})")
    return Playing()


def _selfcheck() -> None:
    import asyncio

    assert not Playing() and Playing(DESKTOP, "x")
    assert Playing(ROOM, "y").label == "y"

    # The regression this module exists for: a track in the music room, nothing on the desktop.
    # Before the owner existed, now_playing asked only the desktop and said "nothing is playing".
    import afon.brain.tools.localplay as lp
    import afon.brain.tools.voicechat as vc

    real_room = vc.room_is_playing
    vc.room_is_playing = lambda: "Miles Davis — So What"

    async def nothing():
        return ""

    async def something():
        return "a local file"

    real = lp.desktop_now_playing
    try:
        lp.desktop_now_playing = nothing
        got = asyncio.run(current())
        assert got.where == ROOM and "Miles" in got.label, got

        # The desktop wins when both answer: it is the one the owner is standing next to.
        lp.desktop_now_playing = something
        assert asyncio.run(current()).where == DESKTOP, asyncio.run(current())

        # An unreachable laptop must not be reported as silence.
        async def boom():
            raise ConnectionError("laptop asleep")

        lp.desktop_now_playing = boom
        assert asyncio.run(current()).where == ROOM, "a dead link swallowed the room's answer"

        vc.room_is_playing = lambda: None
        assert not asyncio.run(current()), "nothing playing anywhere should be falsy"
    finally:
        lp.desktop_now_playing = real
        vc.room_is_playing = real_room
    print("playback self-check OK")


if __name__ == "__main__":
    _selfcheck()
