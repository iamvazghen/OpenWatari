# Adding a new tool (capability) to yourself

This is the most common self-improvement: giving yourself a new skill. Follow the established shape
so it just works, Afon.

## Steps
1. **Create `src/afon/brain/tools/<name>.py`** with two exports:
   - `SCHEMAS` — a list of OpenAI function schemas (`{"type":"function","function":{name, description,
     parameters}}`). The `description` is what *you* read to decide when to call it — make it clear.
   - `HANDLERS` — `{"tool_name": async_handler}` where `handler(args: dict) -> str`.
2. **Make every handler degrade gracefully.** Guard on config; return `not_configured(what, needs)`
   if a key is missing; wrap the body and `return tool_error(what, e)` on failure. Always return a
   speakable `str`.
3. **Register it** in `src/afon/brain/tools/__init__.py`: add the import and append the module to
   `_MODULES`.
4. **Add config** (if it needs a key) in `config.py` + a commented block in `.env.example`.
5. **One line in `memory/tools.md`** so you know you have it (keep it terse — prompt size costs).
6. **If it's outward-facing or destructive**, add the tool name to `CONFIRM_TIER` in
   `brain/proactive.py` so you confirm before running it.
7. **Write `bench/test_phase*.py`** asserting it (a) is registered, (b) degrades gracefully with no
   credentials, (c) any pure helper computes correctly. Register the test in `run_all_tests.py`.
8. `lint()`, `run_tests()`, confirm with Vazghen, `git_commit(...)`.

## A minimal template
```python
from __future__ import annotations
from afon.brain.tools.base import not_configured, tool_error, clip
from afon.config import settings

async def my_tool(args: dict) -> str:
    q = (args.get("query") or "").strip()
    if not q:
        return "What should I look up, sir?"
    if not settings.my_api_key:
        return not_configured("my tool", "an API key (AFON_MY_API_KEY)")
    try:
        ...  # do the work
        return "the spoken result"
    except Exception as e:  # noqa: BLE001
        return tool_error("my tool", e)

SCHEMAS = [{"type": "function", "function": {
    "name": "my_tool", "description": "...", "parameters": {"type": "object",
    "properties": {"query": {"type": "string", "description": "..."}}, "required": ["query"]}}}]
HANDLERS = {"my_tool": my_tool}
```

## For network tools
Use `http_get`/`http_post` from `base.py`, and wrap the call in `CACHE.cached(ns, key, ttl, factory)`
(`brain/cache.py`) if the result is reusable — that's free latency. See `tools/utility.py` for
worked examples.
