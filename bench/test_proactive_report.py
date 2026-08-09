"""The autonomous backlog pass must REPORT what it did (never act silently): runner → reporter → emit.
Hermetic — fakes for the runner/reporter/emit, no Notion, no LLM, no real scheduler tick."""

import asyncio

from afon.brain import scheduler


def _reset():
    scheduler._BACKLOG_RUNNER = None
    scheduler._BACKLOG_REPORTER = None
    scheduler._BRIEFING_EMIT = None
    scheduler._LIVE_SPEAK = None


def test_backlog_pass_reports_via_proactive_emit():
    _reset()
    emitted = []
    attempts = [{"title": "Q3 review", "result": "Drafted outline.", "commented": True}]

    async def fake_runner():
        return attempts

    async def fake_reporter(done):
        assert done == attempts  # the reporter is handed exactly what was done
        return "Sir, I drafted your Q3 outline because it was overdue. Details in the Notion comments."

    async def fake_emit(msg, urgency, speak):
        emitted.append((msg, urgency, speak))
        return "voice"

    scheduler.SCHEDULER.set_backlog_runner(fake_runner)
    scheduler.SCHEDULER.set_backlog_reporter(fake_reporter)
    scheduler.SCHEDULER.set_briefing_emit(fake_emit)

    asyncio.run(scheduler._fire_backlog())

    assert len(emitted) == 1, "the autonomous pass must emit exactly one proactive report"
    assert "Q3" in emitted[0][0] and emitted[0][2] is True  # spoken report reached the emit path
    _reset()


def test_no_report_when_nothing_done():
    _reset()
    emitted = []

    async def empty_runner():
        return []

    async def fake_reporter(done):  # must never be called on an empty pass
        raise AssertionError("reporter called with no attempts")

    async def fake_emit(msg, urgency, speak):
        emitted.append(msg)
        return "voice"

    scheduler.SCHEDULER.set_backlog_runner(empty_runner)
    scheduler.SCHEDULER.set_backlog_reporter(fake_reporter)
    scheduler.SCHEDULER.set_briefing_emit(fake_emit)

    asyncio.run(scheduler._fire_backlog())

    assert emitted == [], "an empty backlog pass must stay silent"
    _reset()


if __name__ == "__main__":
    test_backlog_pass_reports_via_proactive_emit()
    test_no_report_when_nothing_done()
    print("proactive report wiring OK")
