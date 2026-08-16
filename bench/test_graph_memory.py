"""L5b graph memory (TODO 8.6) — no-server sqlite triple store + multi-hop associative recall.

Verifies the lean knowledge graph: triples persist, neighbours resolve, 2-hop traversal reaches
indirectly-linked entities, normalisation dedupes case/whitespace variants, removal works, and the
two lazy tools (link_memory / recall_related) speak correctly. Offline, deterministic — a temp DB.

    uv run python bench/test_graph_memory.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f"  {detail}" if detail and not ok else ""))


def main() -> None:
    from afon.brain.graph import GraphMemory

    db = Path(tempfile.mkdtemp()) / "graph.sqlite"
    g = GraphMemory(db)

    print("[1] add triples + persistence across a fresh handle")
    g.add("Vazghen", "owns", "Lpstrak")
    g.add("Lpstrak", "located in", "Armenia")
    g.add("Lpstrak", "is a", "rabbit farm")
    g.add("Vazghen", "lives in", "Cologne")
    g2 = GraphMemory(db)   # reopen same file — simulates a restart
    check("triples persisted", len(g2.all_triples()) == 4, str(g2.all_triples()))

    print("\n[2] normalisation dedupes case/whitespace and is idempotent")
    g.add("  VAZGHEN ", "OWNS", "lpstrak")   # same triple, different casing/spacing
    check("duplicate (normalised) not double-stored", len(g.all_triples()) == 4,
          str(len(g.all_triples())))
    check("empty parts rejected", g.add("", "rel", "x") is False)

    print("\n[3] neighbours — one hop in either direction")
    nb = g.neighbors("Lpstrak")
    check("Lpstrak has 3 direct edges", len(nb) == 3, str(nb))
    check("edge as object is found too", any(s == "vazghen" and o == "lpstrak" for s, _p, o in nb),
          str(nb))

    print("\n[4] multi-hop: Armenia reaches Vazghen via Lpstrak (2 hops)")
    rel = g.related("Armenia", hops=2)
    check("1 hop reaches Lpstrak", "lpstrak" in rel, str(rel))
    check("2 hops reaches Vazghen", "vazghen" in rel, str(rel))
    check("seed itself excluded", "armenia" not in rel)
    one_hop = g.related("Armenia", hops=1)
    check("1-hop stops before Vazghen", "vazghen" not in one_hop, str(one_hop))

    print("\n[5] describe renders human-readable one-liners")
    desc = g.describe("Vazghen")
    check("describe lists owner relations", any("owns" in d for d in desc), str(desc))

    print("\n[6] removal drops all edges touching an entity")
    removed = g.remove("Cologne")
    check("removing Cologne drops its edge", removed == 1, str(removed))
    check("graph now has 3 triples", len(g.all_triples()) == 3)

    print("\n[7] the lazy tools speak correctly (link_memory / recall_related)")
    from afon.config import settings
    settings.memory_enabled = True
    # Point the tools' shared GRAPH at our temp DB.
    import afon.brain.graph as gmod
    saved = gmod.GRAPH
    gmod.GRAPH = g
    try:
        from afon.brain.tools.graphmem import link_memory, recall_related

        async def _run() -> tuple[str, str, str, str]:
            r_link = await link_memory({"subject": "Lpstrak", "predicate": "raises", "object": "rabbits"})
            r_rel = await recall_related({"entity": "Lpstrak"})
            r_empty = await recall_related({"entity": "Nonexistent Thing"})
            r_missing = await link_memory({"subject": "x"})   # missing predicate/object
            return r_link, r_rel, r_empty, r_missing

        r_link, r_rel, r_empty, r_missing = asyncio.run(_run())
        check("link_memory confirms", "Linked" in r_link, r_link)
        check("recall_related surfaces direct + related", "connected to" in r_rel and "armenia" in r_rel.lower(),
              r_rel)
        check("recall_related on unknown entity is a clean negative", "don't have anything" in r_empty, r_empty)
        check("link_memory rejects incomplete input", "need a subject" in r_missing, r_missing)
    finally:
        gmod.GRAPH = saved

    print("\n[8] a broken DB path degrades to no-op, never crashes")
    broken = GraphMemory(Path(db.parent / "nope" / "cant" / "g.sqlite"))
    check("add on unwritable DB returns False, no raise", broken.add("a", "b", "c") is False)
    check("neighbors on unwritable DB returns []", broken.neighbors("a") == [])

    print("\n[9] the graph tools live in a LAZY group (surface stays lean)")
    from afon.brain.tools import core_tool_schemas, groups_for_text, tool_handlers
    core_names = {s["function"]["name"] for s in core_tool_schemas()}
    check("link_memory not in the every-turn surface", "link_memory" not in core_names)
    check("'graph' group activates on 'related to'", "graph" in groups_for_text("what's related to Lpstrak"))
    check("handlers still resolve", {"link_memory", "recall_related"} <= set(tool_handlers()))

    print("\n[10] the graph is reachable from fused_recall (J3.3b)")
    # Until this landed, the graph was the one store no recall path could see: `recall_related`
    # required the model to already know the entity name. L5b joins it to plain language.
    import asyncio as _aio

    from afon.brain.memory import STORE
    saved = gmod.GRAPH
    gmod.GRAPH = GraphMemory(Path(db.parent / "fused.sqlite"))
    try:
        gmod.GRAPH.add("Lpstrak", "is", "a rabbit farm")
        got = _aio.run(STORE.fused_recall("tell me about Lpstrak", limit=8, layers=("L5b",)))
        check("a graph fact surfaces from a natural-language query",
              any("rabbit farm" in h["text"] for h in got), str(got[:2]))
        check("...tagged L5b so the model can cite the layer",
              all(h["layer"] == "L5b" for h in got), str({h["layer"] for h in got}))
        # The per-turn path must NOT pay for this — that budget is the reason turns feel fast.
        turn = _aio.run(STORE.fused_recall("tell me about Lpstrak", limit=8, layers=("L1", "L2")))
        check("the per-turn L1+L2 path does not touch the graph",
              not any(h["layer"] == "L5b" for h in turn))
    finally:
        gmod.GRAPH = saved

    print("\n[24.F1] rows written under the OLD key are re-keyed, not orphaned")
    # The riskiest edit in 24.F1: changing how a node is keyed makes every existing row
    # unreachable unless they are rewritten. The data would still be there and no lookup could
    # ever find it again — worse than not changing the key at all. Nothing covered this, because
    # every other test starts from an empty database.
    import sqlite3 as _sq
    import tempfile as _tf
    from pathlib import Path as _P

    from afon.brain.graph import GraphMemory as _GM

    with _tf.TemporaryDirectory() as _td:
        _dbp = _P(_td) / "legacy.sqlite"
        _c = _sq.connect(_dbp)
        _c.execute("CREATE TABLE triples (subject TEXT, predicate TEXT, object TEXT, "
                   "PRIMARY KEY(subject, predicate, object))")
        # Exactly what the pre-24.F1 `_norm` would have written: lowercased, whitespace collapsed.
        _c.execute("INSERT INTO triples VALUES ('вазген', 'owns', 'lpstrak')")
        _c.execute("INSERT INTO triples VALUES ('café au lait', 'is a', 'drink')")
        _c.execute("INSERT INTO triples VALUES ('plain', 'stays', 'put')")
        _c.commit()
        _c.close()

        _g = _GM(_dbp)
        _subjects = {s for s, _p, _o in _g.all_triples()}
        check("a Cyrillic node written under the old key is re-keyed",
              "vazgen" in _subjects and "вазген" not in _subjects, str(_subjects))
        check("...and its facts are reachable again", bool(_g.describe("Vazgen")),
              "an unreachable row is data you cannot get back without a migration nobody wrote")
        check("an accented node is re-keyed too", "cafe au lait" in _subjects, str(_subjects))
        check("a row already in canonical form is left alone",
              ("plain", "stays", "put") in _g.all_triples(), str(_g.all_triples()))
        _before = sorted(_g.all_triples())
        _g2 = _GM(_dbp)
        check("re-opening migrates nothing (idempotent)", sorted(_g2.all_triples()) == _before,
              "a migration that runs every open is a migration that can churn forever")

    print("\n[24.F1] the store does not leak a connection per operation")
    # `with sqlite3.connect(...)` commits and does NOT close. Every read and write leaked a
    # handle for the life of the process. Found by a temp-dir teardown failing on Windows, which
    # is the loud version; on Linux it just walks toward the fd limit.
    # Counted, not inferred from whether the file can be deleted — CPython's refcounting collects
    # an unreferenced connection promptly, so the delete succeeds either way and that check could
    # never fail. `dbconn.OPEN` is the difference between "closed" and "happened to be collected".
    from afon.brain import dbconn as _dbc

    with _tf.TemporaryDirectory() as _td2:
        _g3 = _GM(_P(_td2) / "leak.sqlite")
        _before_open = _dbc.OPEN
        for _i in range(25):
            _g3.add(f"node{_i}", "links", "target")
            _g3.describe(f"node{_i}")
        check(f"50 operations leave no connection open (open={_dbc.OPEN})",
              _dbc.OPEN == _before_open,
              "`with sqlite3.connect(...)` commits and does NOT close; on a brain that runs for "
              "weeks that walks toward the fd limit")

    # J3.10, restated for 24.F1. This used to assert that graph._norm and memory._norm produced
    # IDENTICAL output, on the premise that entity keys were written through one and looked up
    # through the other. That premise no longer holds, and saying so plainly matters: memory._norm
    # is used at exactly ONE site - deduplicating learned-fact TEXT - while graph keys now go
    # through the shared `canonical`. Two functions doing different jobs are not required to
    # agree, and holding them to it would block the transliteration folding that 24.F1 needs.
    #
    # What still matters is the property the old check was reaching for: a lookup must not depend
    # on who normalised the string first.
    from afon.brain.graph import _norm as _gnorm
    from afon.brain.memory import _norm as _mnorm
    from afon.shared.entities import canonical
    _cases = ["  Alex  Vardanian ", "GDPR\tArt.\n5", "A B", "Rabbit Farm", "Вазген",
              "MiXeD Case", "", "   ", "café  au   lait", "Mr. John Smith"]
    check("the graph keys entities through the one shared function",
          all(_gnorm(c) == canonical(c) for c in _cases),
          "a private copy is how a key drifts away from everyone else's")
    _unstable = [c for c in _cases if canonical(_mnorm(c)) != canonical(c)]
    check("normalising before a graph lookup cannot change which node is found",
          not _unstable, f"pre-normalising changes the key for {_unstable}")
    _bad = [c for c in _cases if canonical(canonical(c)) != canonical(c)]
    check("the entity key is idempotent (re-keying a stored key is a no-op)",
          not _bad, f"{_bad} - the migration would move rows forever")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
