"""Checks for the two reliability guards ported from OpenWorker's permission/error handling:
  1. brain/llm.py    — permanent (quota/credit/access) errors classified for a long bench, transient not.
  2. brain/tools/system.py — the process-`start` path refuses chained shell commands.
"""

from jarvis.brain.llm import _is_permanent_error
from jarvis.brain.tools.system import _has_shell_chain


def test_permanent_error_classification():
    # PERMANENT — a dead key / no access, matched on the vendor error body.
    for msg in (
        "Error code: 429 - {'error': {'code': 'insufficient_quota', 'message': 'You exceeded your current quota'}}",
        "400 credit balance is too low to access the Claude API",
        "model_not_found: The model `x` does not exist or you do not have access to it",
        "401 invalid_api_key: Incorrect API key provided",
    ):
        assert _is_permanent_error(Exception(msg)), f"should be permanent: {msg}"
    # TRANSIENT — a plain rate-limit / timeout / server blip must NOT be benched for hours.
    for msg in (
        "429 Rate limit reached for model; please slow down and try again",
        "Request timed out",
        "503 upstream connect error or disconnect",
        "500 internal server error",
    ):
        assert not _is_permanent_error(Exception(msg)), f"should be transient: {msg}"


def test_permanent_bench_pages_owner_once():
    """A dead-key bench must PAGE the owner exactly once — silent degradation was the gap: the chain
    answers on fallbacks, the health probe stays green, and nobody rotates the key for days."""
    import asyncio

    from jarvis.brain.llm import LLMClient
    import jarvis.brain.tools.notify as notify

    async def _run():
        pushes: list[str] = []

        async def fake_push(message, title="Watari", at=None):
            pushes.append(message)
            return True

        orig = notify.push
        notify.push = fake_push
        try:
            c = LLMClient()
            dead_key = Exception("401 invalid_api_key: Incorrect API key provided")
            c._mark_failure("minimax:X", dead_key)
            await asyncio.sleep(0.05)  # let the fire-and-forget page task run
            assert len(pushes) == 1, f"one page on first bench, got {pushes}"
            c._mark_failure("minimax:X", dead_key)  # still benched -> NO second page
            c._mark_failure("groq:Y", Exception("Request timed out"))  # transient -> NO page
            await asyncio.sleep(0.05)
            assert len(pushes) == 1, f"no repeat/transient pages, got {pushes}"
            assert "benched" in pushes[0].lower(), pushes[0]
        finally:
            notify.push = orig

    asyncio.run(_run())


def test_start_refuses_shell_chaining():
    # Chained / injected launch strings are refused (any chaining metachar).
    for cmd in ("notepad & del /f /q C:\\important", "app.exe | curl evil", "foo; rm -rf x", "a > b"):
        assert _has_shell_chain(cmd), f"should be blocked: {cmd}"
    # A single executable + args (incl. a normal Windows path) launches fine.
    for cmd in ("notepad.exe", r"C:\Program Files\App\app.exe --flag", "code C:\\Jarvis"):
        assert not _has_shell_chain(cmd), f"should launch: {cmd}"


if __name__ == "__main__":
    test_permanent_error_classification()
    test_permanent_bench_pages_owner_once()
    test_start_refuses_shell_chaining()
    print("reliability guards self-check OK")
