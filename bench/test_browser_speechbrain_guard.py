"""`browser._neutralize_speechbrain_lazy_modules()` — a cross-subsystem monkeypatch with no test.

The hazard, exactly: Playwright calls `inspect.stack()` when an API call fails. `inspect.getmodule()`
walks `sys.modules` and does `hasattr(mod, "__file__")` on every entry. SpeechBrain's LazyModule for
`k2` raises ImportError from that attribute access when `k2` is not installed — so an unrelated,
optional SPEECH dependency masks the real BROWSER error, and the owner is told the wrong thing about
why the browser failed.

Both halves of that condition are true on this machine (speechbrain installed, k2 not), which is why
the patch exists. It breaks silently on either dependency's upgrade:

  * SpeechBrain renames/moves `speechbrain.integrations.k2_fsa` -> the prefix match stops matching
    and the patch quietly does nothing;
  * Playwright stops calling `inspect.stack()` -> the patch becomes dead code nobody removes.

So this file tests the MECHANISM synthetically (works whether or not speechbrain is installed) AND
asserts the real module prefix still exists when it is.

    uv run python bench/test_browser_speechbrain_guard.py
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

passed = failed = 0

PREFIX = "speechbrain.integrations.k2_fsa"


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class HostileLazyModule(types.ModuleType):
    """Stands in for SpeechBrain's LazyModule: touching __file__ raises, exactly as it does."""

    def __getattr__(self, item):
        if item == "__file__":
            raise ImportError("k2 is not installed (simulated LazyModule)")
        raise AttributeError(item)


def main() -> None:
    from afon.brain.tools.browser import _neutralize_speechbrain_lazy_modules as neutralize

    fake_name = PREFIX + ".__test_probe"

    def walk_raises() -> bool:
        """Does Playwright's error path (inspect.stack -> getmodule) blow up?

        `inspect.getmodule` short-circuits on its `modulesbyfile` cache and only falls back to
        scanning `sys.modules` — where the hazard lives — on a miss. The first walk populates that
        cache, so a SECOND walk returns clean whether or not the patch did anything. This check was
        a phantom pass until the cache was cleared here: with the module prefix deliberately broken,
        it still reported "the walk is clean". Clear it, or you are testing the cache.
        """
        inspect.modulesbyfile.clear()
        try:
            for frame in inspect.stack()[:3]:
                inspect.getmodule(frame.frame)
            return False
        except (ImportError, TypeError) as e:
            # TypeError matters as much as ImportError: an EMPTY __file__ placeholder makes
            # `inspect.getfile` raise TypeError('is a built-in module') instead, which masks the
            # real browser error just as thoroughly. Catching only ImportError turned that into a
            # traceback out of this helper rather than a readable FAIL.
            walk_raises.last = f"{type(e).__name__}: {str(e)[:60]}"   # type: ignore[attr-defined]
            return True

    def file_attr(mod):
        """`getattr(mod, "__file__", default)` does NOT work — the default only swallows
        AttributeError, and the hostile module raises ImportError. That IS the hazard."""
        try:
            return mod.__file__
        except ImportError:
            return "<still raises>"
        except AttributeError:
            return "<unset>"

    print("[1] the hazard is real — inspect.getmodule() trips on the hostile module")
    sys.modules[fake_name] = HostileLazyModule(fake_name)
    try:
        check("without the patch, walking the stack raises ImportError", walk_raises(),
              "it did NOT raise — the hazard may be gone, or CPython no longer scans sys.modules "
              "this way; re-verify before deleting the patch")

        print("\n[2] the patch neutralises it")
        neutralize()
        check("after the patch, the same walk is clean", not walk_raises(),
              getattr(walk_raises, "last", ""))
        # The placeholder must be TRUTHY. `inspect.getfile` does
        # `if getattr(object, "__file__", None)`, so an empty string is falsy and the scan raises
        # TypeError('is a built-in module') instead — a different exception, the same masking.
        # The patch shipped with `""` and therefore never worked; this is the check that says so.
        val = file_attr(sys.modules[fake_name])
        check("...because __file__ is now a non-empty placeholder", bool(val) and val != "", repr(val))
    finally:
        sys.modules.pop(fake_name, None)

    print("\n[3] it touches ONLY the modules it claims to")
    victim = types.ModuleType("some_unrelated_module")
    sys.modules["some_unrelated_module"] = victim
    other = HostileLazyModule("speechbrain.utils.something")
    sys.modules["speechbrain.utils.something"] = other
    try:
        neutralize()
        check("an unrelated module keeps its own __file__ (absent)",
              not hasattr(victim, "__file__") or victim.__file__ != "")
        check("a speechbrain module OUTSIDE the k2 prefix is left alone",
              file_attr(other) == "<still raises>",
              "the prefix match is too broad — it patched a module it does not own")
    finally:
        sys.modules.pop("some_unrelated_module", None)
        sys.modules.pop("speechbrain.utils.something", None)

    print("\n[4] it is safe to call when there is nothing to patch")
    for mod in [m for m in sys.modules if m.startswith(PREFIX)]:
        sys.modules.pop(mod, None)
    try:
        neutralize()
        neutralize()   # idempotent: it runs on EVERY browser call and every browser error
        check("calling it with no speechbrain modules loaded is a no-op, not an error", True)
    except Exception as e:  # noqa: BLE001
        check("calling it with no speechbrain modules loaded is a no-op, not an error", False, repr(e))

    print("\n[5] the real module path still exists (the silent-rot check)")
    have_sb = importlib.util.find_spec("speechbrain") is not None
    if not have_sb:
        check("speechbrain absent — prefix check skipped, mechanism above still covered", True)
    else:
        import speechbrain

        p = Path(speechbrain.__file__).parent / "integrations" / "k2_fsa.py"
        d = Path(speechbrain.__file__).parent / "integrations" / "k2_fsa"
        check(f"'{PREFIX}' still exists in the installed speechbrain", p.exists() or d.is_dir(),
              "SpeechBrain moved it — the patch now matches nothing and the ImportError is back")
        check("and k2 is still the optional dep this guards against",
              importlib.util.find_spec("k2") is None,
              "k2 is now installed; the LazyModule no longer raises and the patch may be removable")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
