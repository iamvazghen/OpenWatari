"""Probe: does the brain understand 6 languages and reply in English? (run before+after rule)"""
import asyncio
import sys

sys.path.insert(0, "src")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from afon.brain.agent import AfonAgent  # noqa: E402

SAMPLES = [
    ("English",   "What time is it right now?"),
    ("French",    "Quelle heure est-il maintenant ?"),
    ("German",    "Wie spät ist es gerade?"),
    ("Russian",   "Который сейчас час?"),
    ("Ukrainian", "Котра зараз година?"),
    ("Armenian",  "Ժամը քանի՞սն է հիմա։"),
]


def looks_english(text: str) -> bool:
    # crude: reply is English if it has no Cyrillic/Armenian script and has ASCII letters
    has_non_latin = any("Ѐ" <= c <= "ӿ" or "԰" <= c <= "֏" for c in text)
    has_ascii = any("a" <= c.lower() <= "z" for c in text)
    return has_ascii and not has_non_latin


async def main():
    for lang, text in SAMPLES:
        agent = AfonAgent()  # fresh history each time
        reply = await agent.respond(text)
        print(f"[{lang}] -> {'EN' if looks_english(reply) else 'NON-EN'} | {reply[:90]}")


if __name__ == "__main__":
    asyncio.run(main())
