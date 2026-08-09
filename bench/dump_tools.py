"""Dump the live tool registry as markdown — single source of truth for the docs site.

Run: .venv/Scripts/python.exe bench/dump_tools.py > website/app/_tools.generated.md
Keeps the docs' tool list from drifting from the code (the registry IS the spec).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.tools import CORE_MODULES, _LAZY_GROUPS, tool_schemas  # noqa: E402


def dump_json() -> None:
    """--json: [{name, description, module, group}] for the docs site's generated tool page."""
    import json

    from afon.brain.tools import _MODULES

    module_group = {m.__name__.split(".")[-1]: g for g, mods in _LAZY_GROUPS.items() for m in mods}
    rows = []
    for mod in _MODULES:
        short = mod.__name__.split(".")[-1]
        for s in mod.SCHEMAS:
            fn = s["function"]
            rows.append({
                "name": fn["name"],
                "description": fn.get("description", "").split(". ")[0].rstrip(".") + ".",
                "module": short,
                "group": module_group.get(short, "core"),
            })
    rows.sort(key=lambda r: (r["group"] != "core", r["group"], r["name"]))
    print(json.dumps({"count": len(rows), "tools": rows}, indent=1))


def main() -> None:
    if "--json" in sys.argv:
        dump_json()
        return
    schemas = tool_schemas()
    core = {m.__name__.split(".")[-1] for m in CORE_MODULES}
    groups = {m.__name__.split(".")[-1]: g for g, mods in _LAZY_GROUPS.items() for m in mods}
    print(f"# Afon tools ({len(schemas)} total)\n")
    print("Generated from the live registry by `bench/dump_tools.py` — do not edit by hand.\n")
    for s in sorted(schemas, key=lambda x: x["function"]["name"]):
        fn = s["function"]
        name, desc = fn["name"], fn.get("description", "").split(".")[0]
        print(f"- **`{name}`** — {desc}.")
    print(f"\n_Core (advertised every turn):_ {', '.join(sorted(core))}.")
    print(f"\n_Lazy groups:_ " + "; ".join(f"**{g}** = {', '.join(sorted(m for m, gg in groups.items() if gg == g))}"
                                          for g in sorted(_LAZY_GROUPS)) + ".")


if __name__ == "__main__":
    main()
