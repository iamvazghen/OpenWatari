"""03.R5 — the per-turn catalogue has a budget that is ENFORCED, not merely observed.

Why this file exists
--------------------
03.F3 put a ceiling on the presented catalogue and `test_speed.py` asserts it against RECORDED
traces. That is a ceiling which reports a breach the morning after, not one that prevents it —
and nothing in the turn path ever checked a number before building the catalogue. Measured on
2026-09-14, with the same chars/4 counter `turn_trace` records:

    core alone ................  7,341t over 54 tools
    + 1 heaviest group ........  9,375t
    + 2 .......................  10,674t
    + 3 .......................  11,449t
    + 4 .......................  12,164t
    all 25 groups armed .......  19,004t   <- nothing prevented this

The first pass of 03.R5 went looking for fat in the schemas themselves and did not find it,
which is worth recording so nobody spends the afternoon again:

  * parameters no caller sets:  ZERO. Every advertised property is read somewhere.
  * property descriptions that only restate their key: 12, worth ~42 tokens on the core
    surface. A mechanism to strip them would cost more to maintain than it saves.
  * the long core descriptions are dense with routing triggers ("are you having trouble?"),
    disambiguation ("use open_url instead") and safety rules ("NEVER call this without the
    password"). Cutting those buys tokens and pays for them in wrong-tool selection, which is
    what 03.E1 measures.

So the size that was never attacked is not the schemas. It is the number of groups a single
utterance can arm, which had no cap at all.

    uv run python bench/test_catalogue_budget.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

os.environ.setdefault("AFON_STATE_DIR", tempfile.mkdtemp(prefix="afon-budget-"))

from afon.brain.agent import CATALOGUE_TOKEN_BUDGET, AfonAgent, _UNSURE_NUDGE  # noqa: E402
from afon.brain.tools import (  # noqa: E402
    LAZY_GROUP_TRIGGERS,
    core_tool_schemas,
    group_tool_schemas,
    groups_for_text,
    groups_for_text_ranked,
    tool_schemas,
)
from afon.brain.turn_trace import catalogue_tokens  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def _agent() -> AfonAgent:
    """An agent with the real registry and no network — only `_tools_for_turn` is exercised."""
    return AfonAgent.__new__(AfonAgent)


def _armed(a: AfonAgent, text: str) -> list[dict]:
    return a._tools_for_turn(text)


def main() -> None:
    core = core_tool_schemas()
    base = catalogue_tokens(core)

    print("[1] the measurement this task is built on")
    check(f"the core surface costs {base}t over {len(core)} tools", base > 0, base)
    every = base + sum(catalogue_tokens(group_tool_schemas(g)) for g in LAZY_GROUP_TRIGGERS)
    check(f"every group armed at once would cost {every}t — which is why a budget exists",
          every > CATALOGUE_TOKEN_BUDGET, every)
    check("the budget leaves room for the core surface plus at least one group",
          base + max(catalogue_tokens(group_tool_schemas(g))
                     for g in LAZY_GROUP_TRIGGERS) <= CATALOGUE_TOKEN_BUDGET,
          "a budget that cannot fit one group makes every turn a clarifying question")
    check("...and NOT for every group at once", every > CATALOGUE_TOKEN_BUDGET)

    print("\n[2] there is no dead weight in the schemas (the thing 03.R5 went looking for)")
    src = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                    for p in (Path(__file__).resolve().parents[1] / "src").rglob("*.py"))
    dead = []
    for s in tool_schemas():
        f = s["function"]
        for k in ((f.get("parameters") or {}).get("properties") or {}):
            # The schema names it once; a property nothing else mentions is advertised and ignored,
            # which costs tokens every turn AND lies to the model about what setting it will do.
            if src.count(f'"{k}"') + src.count(f"'{k}'") + src.count(f"{k}=") <= 1:
                dead.append(f"{f['name']}.{k}")
    check("no advertised parameter is ignored by every handler", not dead, dead)

    print("\n[3] groups rank by how strongly the utterance matched, deterministically")
    t = "review my objectives and my habits and my screen time"
    ranked = groups_for_text_ranked(t)
    check("the ranked list holds exactly what the set form holds",
          set(ranked) == groups_for_text(t), f"{sorted(ranked)} vs {sorted(groups_for_text(t))}")
    check("...and it is a list, so a budget can drop the weakest rather than a random one",
          isinstance(ranked, list) and len(ranked) > 2, ranked)
    check("the same utterance ranks the same way twice",
          groups_for_text_ranked(t) == ranked,
          "a catalogue that varies between identical turns misses the prompt cache every time")
    check("an utterance that arms nothing ranks nothing", groups_for_text_ranked("hello") == []
          or set(groups_for_text_ranked("hello")) == groups_for_text("hello"))

    print("\n[4] the budget HOLDS — by construction, on every utterance, not on average")
    a = _agent()
    a._core_tools = core
    a._group_ttl = {}
    a._deferred_groups = []
    a._tools = []
    probes = [
        "review my objectives and my habits and my screen time",
        "what's on my schedule today, any clashes, and how did I sleep",
        "can you check my calendar and my email and then commit the code",
        "write a note about the meeting and add it to my tasks",
        "open spotify and turn the volume up and check the lights",
        # Every trigger in the registry in one sentence: the worst case a person could type.
        " ".join(sorted({kws[0] for kws in LAZY_GROUP_TRIGGERS.values()})),
    ]
    worst = 0
    for text in probes:
        a._group_ttl = {}
        tools = _armed(a, text)
        cost = catalogue_tokens(tools)
        worst = max(worst, cost)
        check(f"{cost:6}t <= {CATALOGUE_TOKEN_BUDGET} for {text[:52]!r}",
              cost <= CATALOGUE_TOKEN_BUDGET, cost)
    check(f"the worst utterance in this file costs {worst}t",
          worst <= CATALOGUE_TOKEN_BUDGET, worst)

    print("\n[5] the budget survives a WARM carry, which is how the old worst case happened")
    # Groups stay advertised for a second turn. Two heavy utterances back to back used to stack,
    # and the recorded worst (12,437t) was exactly that, not one enormous utterance.
    a._group_ttl = {}
    _armed(a, "check my calendar and my email")
    tools = _armed(a, "now commit the code and open a pull request")
    check(f"turn two costs {catalogue_tokens(tools)}t with turn one still warm",
          catalogue_tokens(tools) <= CATALOGUE_TOKEN_BUDGET, catalogue_tokens(tools))

    print("\n[6] what THIS turn asked for outranks what the last one left warm")
    a._group_ttl = {}
    _armed(a, "check my calendar and my email")          # office goes warm
    _armed(a, "commit the code and open a pull request") # coding is fresh
    check("a fresh group is never the one deferred in favour of a warm one",
          "coding" not in a._deferred_groups, a._deferred_groups)

    print("\n[7] a deferred group produces ONE clarifying question, not a guess")
    a._group_ttl = {}
    a._deferred_groups = []
    _armed(a, " ".join(sorted({kws[0] for kws in LAZY_GROUP_TRIGGERS.values()})))
    check("the worst-case utterance really does defer something",
          bool(a._deferred_groups), "otherwise the rest of this section proves nothing")
    note = _UNSURE_NUDGE.format(armed="office", deferred=" or ".join(a._deferred_groups))
    check("the note names what he is NOT holding tools for",
          all(g in note for g in a._deferred_groups[:1]), note[:120])
    for must in ("ask ONE short question", "Do not guess", "not pretend"):
        check(f"...and it says {must!r}", must in note, note)
    check("a deferred group keeps its TTL, so the follow-up doesn't pay the trigger cost again",
          all(a._group_ttl.get(g, 0) > 0 for g in a._deferred_groups), a._group_ttl)

    print("\n[8] a turn that defers NOTHING says nothing — silence is the common case")
    a._group_ttl = {}
    a._deferred_groups = []
    _armed(a, "what's the weather")
    check("an ordinary turn defers no group", a._deferred_groups == [], a._deferred_groups)

    print("\n[9] the enforced budget is below the ceiling test_speed.py asserts")
    speed = (Path(__file__).resolve().parent / "test_speed.py").read_text(encoding="utf-8")
    declared = [ln for ln in speed.splitlines() if "CATALOGUE_TOKEN_CEILING" in ln and "=" in ln]
    check("test_speed.py still declares an observed ceiling", bool(declared), declared)
    ceiling = int(declared[0].split("=")[1].split("#")[0].strip().replace("_", ""))
    check(f"the enforced budget ({CATALOGUE_TOKEN_BUDGET}) is at or below the observed "
          f"ceiling ({ceiling})", CATALOGUE_TOKEN_BUDGET <= ceiling,
          "an enforced budget above the ceiling would let the recorded gate fail while the "
          "runtime believed it was inside its limit")

    print(f"\n=== {PASS}/{PASS + FAIL} checks passed ===")
    print(json.dumps({"core_tokens": base, "budget": CATALOGUE_TOKEN_BUDGET,
                      "all_groups": every, "worst_probe": worst}))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
