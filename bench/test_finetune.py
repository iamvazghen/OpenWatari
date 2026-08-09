"""Fine-tuning regression guard — locks in the efficiency wins from fine-tuning.md.

Offline & deterministic. Verifies the three TUNE items stay fixed:
  * Item 1 — system prompt stays lean (<= 2000 tok) and still carries the load-bearing context;
             the duplicated tools.md / openclaw-fleet.md are NOT injected every turn.
  * Item 2 — the per-turn tool surface (core) is <= 48, while the FULL registry still holds every
             tool (no capability removed); lazy groups activate on the right utterances.
  * Item 3 — the primary model is the fast one (TTFT), with 70b kept as a fallback.

    uv run python bench/test_finetune.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> None:
    from afon.brain.context import build_system_prompt, load_memory_files
    from afon.brain.tools import (
        core_tool_schemas,
        groups_for_text,
        tool_handlers,
        tool_names,
    )
    from afon.config import settings

    print("[1] Item 1 — system prompt is lean but complete")
    sp = build_system_prompt()
    tok = len(sp) // 4
    check(f"prompt <= 2000 tok (got ~{tok})", tok <= 2000, f"{tok} tok")
    # Worst case = the REAL prompt with the current digest removed (base = everything else, incl. the
    # catalog + operating rules that follow the digest), plus a FULL, MAX-LENGTH digest's headroom
    # (memory_digest_max facts, each capped at memory_digest_fact_chars + "- " + newline). This is the
    # honest bound: it holds even if every learned fact grows to the per-fact cap. The per-fact cap is
    # what makes this bound real (context._learned_digest truncates each line).
    from afon.brain.context import _learned_digest
    digest = _learned_digest()
    base_no_digest_chars = len(sp) - len(digest)
    per_fact = settings.memory_digest_fact_chars + 3   # "- " prefix + newline
    worst = (base_no_digest_chars + settings.memory_digest_max * per_fact) // 4
    check(f"prompt stays <= 2000 tok with a full max-length digest (~{worst})", worst <= 2000, f"{worst} tok")
    # Still carries identity + principal + the proactive mandate.
    check("persona present (Afon)", "Afon" in sp)
    check("principal present (Vazghen)", "Vazghen" in sp)
    check("proactive mandate present", "proactive" in sp.lower() or "initiate" in sp.lower())
    check("delegation rule present (ispir)", "ispir" in sp.lower())
    # The two demoted files are NOT injected as memory sections every turn.
    always_on = {name for name, _ in load_memory_files()}
    check("tools.md NOT always-on", "tools.md" not in always_on, str(always_on))
    check("openclaw-fleet.md NOT always-on", "openclaw-fleet.md" not in always_on, str(always_on))
    check("environment.md NOT always-on", "environment.md" not in always_on, str(always_on))

    print("\n[2] Item 2 — lean per-turn surface, full registry intact")
    core = len(core_tool_schemas()) + 2          # + get_time, delegate_to_fleet
    full = len(tool_names())
    # Discipline guard: the per-turn surface must stay a lean SUBSET of the full registry (the point
    # of lazy groups). The absolute number tracks legitimate core growth across phases — the task
    # co-pilot (plan_task/execute_task, advertised as agent schemas) and media control (media_pause)
    # added real capability — so the guard is expressed relatively: core is well under 60% of the
    # full set, with an absolute ceiling to catch runaway growth. (Was 48 when core was ~46; 56 since
    # now_playing, which can't be lazy: lazy groups are per-MODULE, and demoting localplay would take
    # stop_music with it — "stop the music" must never wait a turn for a trigger word to match.)
    check(f"per-turn surface <= 56 (got {core})", core <= 56, f"{core}")
    check(f"per-turn surface stays a subset (<60% of full): {core}/{full}", core < 0.6 * full, f"{core}/{full}")
    check(f"full registry intact (>= 63, got {full})", full >= 63, f"{full}")
    # No capability removed: every lazy tool is still resolvable to a handler.
    handlers = tool_handlers()
    lazy_examples = ["read_source", "git_commit", "notion_search", "read_email", "list_events",
                     "ha_call", "play_in_music_room"]
    check("all lazy tools still have handlers", all(t in handlers for t in lazy_examples),
          str([t for t in lazy_examples if t not in handlers]))

    # H2.3 — every schema that takes arguments must SAY which are mandatory. 12 declared properties
    # with no `required` key at all, so the model was left to guess; an omitted `required` reads as
    # "nothing is needed", which is right for a screenshot and wrong for approve_action. An explicit
    # empty list is fine — the point is that it's a decision, not an oversight.
    from afon.brain.tools import tool_schemas
    undeclared = [s["function"]["name"] for s in tool_schemas()
                  if (s["function"].get("parameters") or {}).get("properties")
                  and "required" not in (s["function"].get("parameters") or {})]
    check("every schema with properties declares `required`", not undeclared, str(undeclared))
    # Tools that mutate or execute must name their identifier, or the model can fire them blind.
    must_require = {"approve_action": "topic", "reject_action": "topic",
                    "complete_objective": "topic", "drop_objective": "topic"}
    by_name = {s["function"]["name"]: s["function"] for s in tool_schemas()}
    for tool, arg in must_require.items():
        got = (by_name[tool].get("parameters") or {}).get("required") or []
        check(f"{tool} requires '{arg}'", arg in got, str(got))

    # H2.4 — a tool description is the ONLY text the model gets to choose between 136 tools, and
    # wrong-tool selection is the dominant benchmark failure. 39 were under 120 chars, several so
    # bare they collided outright ("Forget a saved macro." vs the `forget` memory tool). 120 is a
    # floor for "says what it does AND when to pick it over its neighbour", not a target.
    stubs = sorted((len(f["description"]), n) for n, f in by_name.items()
                   if len(f.get("description") or "") < 120)
    check("no tool description is a stub (>= 120 chars)", not stubs, str(stubs[:6]))
    # ...but they are prefilled EVERY turn, so richer text is not free. Bound the total so a future
    # description spree can't quietly re-inflate the per-turn prompt that Phase C worked to shrink.
    core_chars = sum(len(s["function"].get("description") or "") for s in core_tool_schemas())
    check(f"core tool catalog stays under ~3.6k tok (~{core_chars // 4})", core_chars < 14_500,
          f"{core_chars} chars")

    # H2.7 — everything in tools/ is a tool. `mynews.py` lived here exposing zero schemas and zero
    # handlers (it is a proactive signal source, now in brain/), which quietly made that sentence
    # false and left the package a mix of two unrelated things. `base` is the one honest exception:
    # shared helpers, imported by the rest.
    import importlib
    from pathlib import Path
    tools_dir = Path(__file__).resolve().parents[1] / "src" / "afon" / "brain" / "tools"
    strays = []
    for p in sorted(tools_dir.glob("*.py")):
        if p.stem in ("__init__", "base"):
            continue
        m = importlib.import_module(f"afon.brain.tools.{p.stem}")
        if not (getattr(m, "SCHEMAS", None) or getattr(m, "HANDLERS", None)
                or getattr(m, "LOCAL_HANDLERS", None)):
            strays.append(p.stem)
    check("every module in tools/ actually exposes tools", not strays, str(strays))

    # H2.8 — the tools/__init__ <-> macros cycle. `__init__` imports macros at module level, and
    # macros needs tool_handlers() back out of `__init__`; it survives ONLY because every such
    # import sits inside a function. Promote one to the top of the file and `import
    # afon.brain.tools` fails on a half-initialised module — brain startup dies, with a traceback
    # pointing at __init__ rather than at the line someone just moved. A comment can't stop that;
    # this can. AST, not grep, so an import inside a function is correctly ignored.
    import ast
    macros_src = (tools_dir / "macros.py").read_text(encoding="utf-8")
    top_level_cycle = [
        n.module for n in ast.parse(macros_src).body
        if isinstance(n, ast.ImportFrom) and (n.module or "") == "afon.brain.tools"
    ]
    check("macros.py imports tools/__init__ only INSIDE functions", not top_level_cycle,
          f"top-level import of {top_level_cycle} would break brain startup")

    print("\n[3] Item 2 — lazy groups activate on the right utterances")
    check("'commit my code' -> coding", "coding" in groups_for_text("commit my code to git"))
    check("'any new email?' -> office", "office" in groups_for_text("any new email?"))
    check("'what's on my calendar' -> office", "office" in groups_for_text("what's on my calendar today"))
    check("'turn on the light' -> home", "home" in groups_for_text("turn on the kitchen light"))
    check("'what's the weather' -> no lazy group", groups_for_text("what's the weather in Yerevan") == set())
    check("'what time is it' -> no lazy group", groups_for_text("what time is it") == set())

    print("\n[4] Item 2 — the agent advertises core by default, expands on a coding turn")
    from afon.brain.agent import AfonAgent

    a = AfonAgent()
    default_names = {s["function"]["name"] for s in a._tools}
    check("default surface == core (no lazy tools yet)", "git_commit" not in default_names)
    check("default surface has everyday tools", {"search_vault", "web_search", "get_time"} <= default_names)
    turn = a._tools_for_turn("read your config.py and run the tests")
    turn_names = {s["function"]["name"] for s in turn}
    check("coding turn now advertises coding tools", {"read_source", "run_tests"} <= turn_names)
    # A following unrelated turn: coding stays warm one turn, then decays.
    a._tools_for_turn("thanks")                     # warm (ttl 2 -> 1)
    a._tools_for_turn("what's the weather?")        # decays (ttl 1 -> 0)
    after = {s["function"]["name"] for s in a._tools_for_turn("hello")}
    check("coding tools decay back out when unused", "read_source" not in after, str(sorted(after))[:80])

    print("\n[5] Item 3 — MiniMax primary, free tier behind it, Vercel gateway as the last resort")
    # Policy change 2026-07-28 (owner's call, supersedes the 2026-06-14 latency-only pick):
    # llama-3.3-70b-versatile is still the FASTEST (0.23s TTFT vs MiniMax 0.77s), but it lives on
    # Groq's free daily quota — and when that quota and Gemini's rate limit landed together the
    # WHOLE chain exhausted and Afon went mute mid-turn (40 x "brain turn failed" in 8 days).
    # A paid, quota-independent primary is worth ~0.5s of TTFT. Ordering is therefore:
    #   MiniMax (paid, reliable)  ->  free/self-hosted tier  ->  Vercel AI Gateway (paid backstop).
    # The gateway must stay LAST so it is only ever billed when everything else has already failed.
    primary_model = settings.llm_primary_model
    check("primary is the paid, quota-independent MiniMax",
          primary_model.startswith("minimax:"), primary_model)
    check("the dumb 8b-instant is not the primary",
          primary_model.split(":", 1)[-1] != "llama-3.1-8b-instant")
    check("primary is first in the chain", settings.llm_chain[0] == settings.llm_primary_model)
    check("chain is provider-diverse (>=2 distinct providers as fallbacks)",
          len(settings.llm_chain) >= 3, str(settings.llm_chain))
    # The backstop: present, and the CONTIGUOUS TAIL of the chain (2026-07-28: two cheap fast
    # non-reasoning gateway models — deepseek-v3.2 then qwen3.5-flash — replace the pricier haiku).
    # A vercel: entry anywhere before the tail would bill the gateway while free models were healthy.
    chain = settings.llm_chain
    vercel_at = [i for i, m in enumerate(chain) if m.startswith("vercel:")]
    check("Vercel AI Gateway is wired into the chain", bool(vercel_at), str(chain))
    check("Vercel AI Gateway entries are the ABSOLUTE last resort (contiguous tail)",
          vercel_at == list(range(len(chain) - len(vercel_at), len(chain))),
          f"at {vercel_at} of {len(chain)}")
    check("gateway tail is cheap + non-reasoning (no thinking/r1/claude/gpt tiers)",
          all(not any(x in chain[i].lower() for x in ("thinking", "r1", "claude", "gpt-5", "opus"))
              for i in vercel_at), str([chain[i] for i in vercel_at]))
    check("a free/groq tier sits between the primary and the gateway",
          any(m.startswith(("groq:", "gemini", "mistral", "ollama:"))
              for m in chain[1:vercel_at[0]] if vercel_at), str(chain))

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
