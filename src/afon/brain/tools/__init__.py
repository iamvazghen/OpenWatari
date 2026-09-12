"""Afon's Phase 3 tool registry — knowledge & channels.

Each tool module exposes ``SCHEMAS`` (OpenAI function schemas the LLM sees) and ``HANDLERS``
(name -> async ``handler(args: dict) -> str``). The agent merges these with its built-ins
(get_time, delegate_to_fleet). Every handler degrades gracefully when its integration isn't
configured, so the full set can always be registered safely (see ``tools/base.py``).

Adding a capability = drop a module here and list it in ``_MODULES``.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from afon.brain.tools import (
    activity,
    approvals,
    browser,
    calendar,
    audioout,
    camera,
    channels,
    coaching,
    coding,
    composio,
    contacts,
    day_shape,
    diagnose,
    documents,
    fitness,
    gmail,
    graphmem,
    inbox,
    localplay,
    macros,
    maps,
    memory,
    multimodal,
    music,
    notify,
    notion,
    objectives,
    phone,
    protocols,
    reminders,
    relationship,
    routines,
    skills,
    smarthome,
    system,
    tasks,
    telegram,
    undo,
    utility,
    vault,
    voicechat,
    web,
    wolfram,
)

_MODULES = [vault, memory, web, telegram, voicechat, music, localplay, system, browser,
            protocols, reminders, notify, gmail, calendar, smarthome, utility, routines,
            coding, skills, notion, tasks, contacts, documents, composio, channels,
            macros, multimodal, undo, graphmem, activity, coaching, camera, objectives,
            approvals, relationship, phone, maps, wolfram, fitness, diagnose,
            audioout, day_shape, inbox]  # noqa: E501

Handler = Callable[[dict], Awaitable[str]]

# --- Lazy tool groups (fine-tuning.md Item 2) ---------------------------------------------
# Every handler is ALWAYS in the registry below (so tests, the proactive engine, and direct
# handler calls keep working). These groups are about what the AGENT *advertises to the model on a
# given turn*: low-frequency modules are held back until the turn actually needs them, keeping the
# per-turn schema surface lean. No capability is removed — a group simply lights up when relevant.
_LAZY_GROUPS: dict[str, list] = {
    "coding": [coding],                  # read/write source, tests, lint, git — only for dev work
    "office": [notion, gmail, calendar],  # email, calendar, Notion — when he asks about them
    "home": [smarthome, voicechat],      # smart-home control + Telegram music-room streaming
    "docs": [documents],                 # read a local document + answer questions grounded in it
    "apps": [composio],                  # 250+ external apps via Composio (GitHub/Slack/Drive/...)
    "channels": [channels],              # YouTube channel ops (list / random / latest) — T1
    "macros": [macros],                  # User-defined macro sequences + if_then — T2
    "screen": [multimodal],              # screenshot + OCR the screen — T6 (only on "look at my screen")
    "camera": [camera],                  # webcam vision (look_around) + visual presence — Phase 3.2/3.3
    "undo": [undo],                      # undo_last / list_recent_actions — T7 (only on "undo that")
    "graph": [graphmem],                 # L5b structured relations + multi-hop recall (only when linking)
    "activity": [activity],              # screen-time / current activity / tracking privacy switch — Phase 0
    "coaching": [coaching],              # field skill reviews + progress (e.g. German quiz) — Phase 2
    "objectives": [objectives],          # multi-day objectives Afon drives (assign/status/…) — Phase 4.1
    "approvals": [approvals],            # approve/reject the outward steps autonomous work deferred — 4.2
    "relationship": [relationship],      # sensitivities / running jokes / how-we-stand — Phase 6.2
    "phone": [phone],                    # real phone calls via Twilio (parked until account) — 2026-07-28
    "places": [maps],                    # live travel time + place search (Google Maps)
    "compute": [wolfram],                # exact math/facts via Wolfram Alpha
    "vitals": [fitness],                 # sleep/steps/heart from the wearable via Google Fit
    "audioout": [audioout],              # which speaker/headphones Afon talks through — H1.3
    "dayshape": [day_shape],           # draft routines / check a day holds together — S21
}
# Substring triggers (lowercased) that activate a group for a turn. Broad on purpose — a miss just
# means a one-turn delay (the follow-up usually contains the word, and groups stay warm one turn).
LAZY_GROUP_TRIGGERS: dict[str, tuple[str, ...]] = {
    "coding": ("code", "coding", "source", "function", "bug", "refactor", "commit", "git ",
               "lint", "unit test", "run the test", "run tests", "repo", "push", "branch",
               "revert", "your code", "self-improve", "self improve", "improve yourself",
               "pull request", "diff", "the suite", "issue", "file a bug", "open an issue",
               "github issue", "ticket"),
    "office": ("email", "e-mail", "mail", "inbox", "gmail", "draft", "calendar", "schedule",
               "event", "meeting", "appointment", "agenda", "notion", "document", "page",
               "task list", "my tasks", "task", "tasks", "deadline", "due", "to-do", "todo",
               "to do", "on my plate", "what's due", "whats due", "what do i need to do",
               "what needs", "dashboard", "overdue"),
    "home": ("smart home", "home assistant", "light", "lamp", "thermostat", "heating", "lock",
             "unlock", "music room", "voice chat", "stream music", "play in the room"),
    "docs": ("document", "this file", "read this", "the pdf", "a pdf", "the doc", "this doc",
             "in the file", "ask the document", "close the document", "read the file",
             "this report", "the attachment"),
    "apps": ("github", "gitlab", "slack", "discord", "google drive", "gdrive", "google doc",
             "google sheet", "spreadsheet", "stripe", "linear", "jira", "trello", "asana",
             "airtable", "reddit", "youtube", "linkedin", "instagram", "coinbase", "supabase",
             "hubspot", "salesforce", "calendly", "an issue", "a pr", "pull request",
             "post a message", "send a message to", "add a row", "create a channel", "in slack",
             "on github", "to slack", "a repo", "my repos"),
    "channels": ("youtube channel", "channel ", "from channel", "random video", "random from",
                 "latest from", "latest video", "what's new on", "new from", "upload from",
                 "of videos", "lofi girl", "veritasium", "mrbeast"),
    "macros": ("macro", "macros", "routine", "morning routine", "evening routine", "shortcut",
               "if then", "if ", "branching", "every morning", "every day at", "recurring",
               "set up a"),
    "screen": ("my screen", "the screen", "look at my", "what's on screen", "whats on screen",
               "screenshot", "read the screen", "on my display", "see my screen", "what am i looking"),
    "camera": ("camera", "webcam", "look around", "in front of me", "can you see", "look through",
               "am i at my desk", "are you watching", "who's here", "who is here", "with your eyes",
               "point the camera", "through the camera", "look at this", "learn my face",
               "recognise me", "recognize me", "remember my face", "remember what i look like",
               "do you recognise", "do you recognize", "who am i", "enroll my face", "enrol my face"),
    "undo": ("undo", "undo that", "revert that", "take that back", "roll back", "rollback",
             "what did you just do", "recent actions", "last action"),
    "graph": ("related to", "connected to", "connection between", "link ", "linked to", "how is",
              "how are", "associate", "association", "relationship between", "what's tied to",
              "whats tied to", "map out", "knowledge graph"),
    "activity": ("screen time", "screentime", "screen-time", "what am i doing", "what have i been",
                 "how long have i", "how much time", "on my computer", "on the computer", "focused",
                 "productivity", "how long was i", "stop watching my screen", "pause tracking",
                 "pause activity", "resume tracking", "activity tracking", "am i wasting"),
    "coaching": ("quiz me", "test me", "test my", "quiz my", "practise", "practice", "my german",
                 "my french", "my spanish", "review my", "my level", "how's my", "hows my",
                 "am i improving", "am i progressing", "skill", "flashcard", "coaching", "learn german",
                 # "check my" was here and is FAR too broad: "check my email", "check my calendar"
                 # and "check my tasks" are among the most common things the owner says, and each
                 # one loaded five German-quiz tools onto the turn for nothing. Measured at ~700
                 # wasted prompt tokens per occurrence.
                 "language practice", "check my level", "check my progress", "check my german",
                 "check my french", "check my spanish", "vocab", "vocabulary"),
    "objectives": ("objective", "objectives", "take this on", "take on this", "own this", "drive this",
                   "drive it to", "make it happen", "over the next", "across days", "work on it over",
                   "what are you working on", "what are you driving", "your objectives", "how's the",
                   "hows the", "progress on", "get it launch", "launch-ready", "launch ready",
                   "carry this", "keep pushing on", "long-term goal", "multi-day"),
    "approvals": ("approve", "approval", "approvals", "needs my sign", "sign-off", "sign off",
                  "waiting on me", "waiting for me", "pending action", "pending actions", "anything pending",
                  "go ahead and send", "go ahead and", "permission to", "reject that", "don't send it",
                  "dont send it", "drop that one", "let it through", "authorise", "authorize"),
    "relationship": ("sensitive subject", "sore subject", "sore spot", "go easy on", "be gentle about",
                     "touchy subject", "running joke", "inside joke", "our joke", "read the room",
                     "how am i doing", "how are we doing", "handle gently", "don't bring up", "dont bring up"),
    "phone": ("call ", "phone", "ring ", "dial", "call the", "call my"),
    # Phrasing matters here: "how long would it take me to drive from X to Y" matched NONE of the
    # original needles ("how long to", "drive to"), so the group never loaded and the model answered a
    # deterministic routing question with a web search (observed 2026-07-30, 6.3s and a wrong ETA).
    "places": ("how long to", "how long would", "how long does it take", "how long will it take",
               "how far", "directions", "traffic", "route to", "eta", "drive to", "drive from",
               "driving", "by car", "walk to", "cycle to", "travel time", "get there",
               "get to the", "nearest", "find a restaurant", "find a place", "a good restaurant",
               "places near", "close by", "on the way", "when should i leave"),
    "compute": ("calculate", "compute", "convert", "how many", "what's the square", "whats the square",
                "percent of", "compound", "equation", "solve", "integral", "derivative",
                "in kilograms", "in pounds", "in euros", "in dollars", "sunset", "sunrise"),
    "vitals": ("sleep", "slept", "steps", "heart rate", "vitals", "how active", "my workout data",
               "recovery", "resting heart"),
    # Distinctive enough to trigger reliably, which is why this can be lazy rather than eating a
    # slot in the per-turn surface. "out loud" is deliberately absent — it belongs to playback.
    "audioout": ("headphone", "headphones", "airpod", "airpods", "speaker", "speakers", "earbuds",
                 "switch to my", "play through", "output device", "sound device", "audio output",
                 "talk through", "speak through", "which speakers",
                 # 42.F3 — volume lives here because it is a property of the output, and these
                 # words are distinctive enough to trigger without taxing the per-turn surface.
                 "louder", "quieter", "volume", "mute", "unmute", "turn it up", "turn it down",
                 "too loud", "too quiet", "how loud", "turn the sound"),
    "dayshape": ("my routines", "my routine", "draft my", "propose my",
                 "adopt the routine", "adopt my routine", "shape of my day",
                 "does my day", "will my day", "double-booked", "double booked",
                 "check my day", "my day work", "clash", "clashes",
                 "back to back", "back-to-back"),
}


def _schemas_of(mods: list) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for mod in mods:
        out.extend(mod.SCHEMAS)
    return out


def tool_schemas() -> list[dict[str, Any]]:
    """The FULL registry of schemas (every group). Used by tests + the report's registry view."""
    return _schemas_of(_MODULES)


def core_tool_schemas() -> list[dict[str, Any]]:
    """Schemas advertised on every turn (core groups only) — the lean per-turn surface."""
    return _schemas_of(CORE_MODULES)


def group_tool_schemas(group: str) -> list[dict[str, Any]]:
    """Schemas for one lazy group, added to a turn when that group activates."""
    return _schemas_of(_LAZY_GROUPS.get(group, []))


def groups_for_text(text: str) -> set[str]:
    """Which lazy groups a user utterance should activate (substring trigger match)."""
    t = (text or "").lower()
    return {g for g, kws in LAZY_GROUP_TRIGGERS.items() if any(k in t for k in kws)}


def schemas_by_name(names) -> list[dict[str, Any]]:
    """Schemas for the given tool names, pulled from the FULL registry (any group). Used by the
    B1 intent router to narrow a turn to exactly the right tool(s). Silently skips unknown names."""
    want = set(names)
    return [s for s in tool_schemas() if s["function"]["name"] in want]


def tool_handlers() -> dict[str, Handler]:
    handlers: dict[str, Handler] = {}
    for mod in _MODULES:
        handlers.update(mod.HANDLERS)
    return handlers


def tool_names() -> list[str]:
    return [s["function"]["name"] for s in tool_schemas()]


_LAZY_MODULES = {m for mods in _LAZY_GROUPS.values() for m in mods}
# Core modules are advertised on every turn; lazy ones only when their group is active.
CORE_MODULES = [m for m in _MODULES if m not in _LAZY_MODULES]
