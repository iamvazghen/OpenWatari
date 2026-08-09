"""Streaming-latency guard: a pure-chatter turn carries NO tool surface (so the model answers without
the ~10k-token, ~1.7s tool-schema prefill), while any tool-needing turn keeps its tools.

The detector must be HIGH-PRECISION: a false positive strips tools from a turn that needed one. So the
key assertions are the negatives — tool/knowledge/command turns must NOT be classified as pure chat.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.agent import AfonAgent, _is_pure_chat  # noqa: E402

_ok = _fail = 0


def check(cond, label):
    global _ok, _fail
    if cond:
        _ok += 1
    else:
        _fail += 1
        print(f"  FAIL: {label}")


# 1) high-confidence chatter -> pure chat (no tools)
for t in ["hi", "hey Afon", "hello there", "good morning", "how are you?", "how's it going",
          "thanks", "thank you so much, sir", "cheers", "nice one", "well done", "good job",
          "tell me a joke", "you're the best", "goodnight", "see you later", "love you",
          "how do you feel?", "are you there?", "got it", "makes sense", "never mind", "lol"]:
    check(_is_pure_chat(t), f"chatter classified as pure chat: {t!r}")

# 2) tool / command / knowledge turns must NOT be pure chat (would strip needed tools)
for t in ["what's on my calendar today?", "do I have any new emails?", "any unread telegram?",
          "remind me to stretch in 90 minutes", "send an email to Bob saying hi",
          "what's the weather in Yerevan?", "what's the price of Bitcoin?",
          "what is the capital of France?", "explain quantum computing",
          "what do you think about my calendar?", "how am I doing on my german?",
          "look at my screen", "undo that", "add a task called groceries",
          "search the web for ferry times", "remember my flight is July 3rd",
          "thanks, now check my email", "nice, open example.com"]:
    check(not _is_pure_chat(t), f"tool/knowledge turn NOT pure chat: {t!r}")

# 3) end-to-end: _tools_for_turn carries zero tools on chatter, full surface on a tool turn
a = AfonAgent()
a._self_improve = False
check(a._tools_for_turn("how are you?") == [], "chatter turn advertises zero tools")
check(len(a._tools_for_turn("what's the weather in Paris?")) > 10, "a tool-ish turn keeps a real surface")


# ── K2 · lazy-group triggers must not fire on unrelated common phrases ─────
# "check my" was a COACHING trigger, so "check my email" / "check my calendar" / "check my tasks"
# — three of the most common things the owner says — each loaded five German-quiz tools onto the
# turn. Nothing failed; it just quietly cost ~565 prompt tokens every time. Measured, not guessed.
from afon.brain.tools import groups_for_text  # noqa: E402

# NB: this file's check() is check(cond, label) — cond FIRST, two args only. Written the other
# way round it does not error, it PASSES, because a non-empty label string is truthy.
for phrase in ("check my email", "check my calendar", "check my tasks", "check my messages"):
    check("coaching" not in groups_for_text(phrase),
          f"{phrase!r} must not load the coaching group -> {sorted(groups_for_text(phrase))}")

# ...while the coaching group still activates when coaching is genuinely meant.
for phrase in ("quiz me on German", "how's my German going", "check my level in German",
               "test my French"):
    check("coaching" in groups_for_text(phrase),
          f"{phrase!r} must still load coaching -> {sorted(groups_for_text(phrase))}")

print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
