"""J4.5 — the browser clients are checked statically, and never against the brain they call.

`clients/iphone/index.html` and `clients/hud/index.html` are the two surfaces a human touches
without Afon's voice: one sends audio to `/talk`, the other polls `/hud.json`. Both talk to the
brain's HTTP handler in `brain/server.py`, and nothing connected the two sides — the existing client
test verifies the page in isolation ("statically + server injection"), which cannot see a route that
was renamed, a method that changed, or an auth scheme the server stopped accepting.

That failure is unusually quiet. The page keeps loading; the fetch returns 404 or 401; the client
shows its own polite error, which is exactly what it would show if the brain were simply down. The
owner concludes "the phone client is flaky" and nobody bisects it.

Three couplings, all of them silent when broken:

  * **Route.** Every path the clients fetch must be handled by the server.
  * **Method.** `/talk` must be served by `do_POST` and `/hud.json` by `do_GET` — a route that moves
    between the two handlers still exists, and still 404s for the client.
  * **Auth.** The iPhone client sends `Authorization: Bearer …`; the HUD appends `?token=…` because
    it is a plain page poll. `_post_authorized` must accept BOTH. If it is ever tightened to headers
    only, the HUD 401s and looks like a token problem rather than a code change.

Static by construction: it reads the two HTML files and the server module as text. No brain is
started, no port is opened.

    uv run python bench/test_client_endpoints.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]

passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


#: fetch("/path" …) / fetch('/path' …) — the leading quote+slash is what makes it a server route
#: rather than a relative asset.
_FETCH = re.compile(r"""fetch\(\s*["'](/[a-zA-Z0-9_.\-/]*)["']""")
_METHOD = re.compile(r"""method:\s*["'](GET|POST|PUT|DELETE)["']""", re.IGNORECASE)


def _handler_body(src: str, name: str) -> str:
    """The text of one do_GET / do_POST method, so a route can be attributed to a verb."""
    start = src.find(f"def {name}(")
    if start == -1:
        return ""
    rest = src[start:]
    nxt = re.search(r"\n        def [a-zA-Z_]+\(", rest[1:])
    return rest[: nxt.start()] if nxt else rest


def main() -> None:
    server_src = (ROOT / "src" / "afon" / "brain" / "server.py").read_text(encoding="utf-8")
    pages = sorted((ROOT / "clients").rglob("index.html"))

    print("[1] the surfaces this checks")
    check(len(pages) >= 2, f"client pages found ({len(pages)})", str([p.parent.name for p in pages]))
    do_get, do_post = _handler_body(server_src, "do_GET"), _handler_body(server_src, "do_POST")
    check(bool(do_get) and bool(do_post), "server exposes do_GET and do_POST",
          f"get={len(do_get)} post={len(do_post)}")

    print("\n[2] every path a client fetches is routed by the brain")
    calls: list[tuple[str, str, str]] = []      # (page, path, method)
    for p in pages:
        text = p.read_text(encoding="utf-8")
        for m in _FETCH.finditer(text):
            path = m.group(1)
            tail = text[m.end(): m.end() + 200]
            verb = (_METHOD.search(tail).group(1).upper() if _METHOD.search(tail) else "GET")
            calls.append((p.parent.name, path, verb))
    check(bool(calls), f"client fetches found ({len(calls)})", str(calls))
    missing = [f"{pg}: {path}" for pg, path, _v in calls if f'"{path}"' not in server_src]
    check(not missing, "every fetched path appears as a route in server.py", str(missing))

    print("\n[3] …by the right verb (a route that moves handlers still 404s)")
    wrong = []
    for pg, path, verb in calls:
        body = do_post if verb == "POST" else do_get
        if f'"{path}"' not in body:
            wrong.append(f"{pg}: {verb} {path} is not handled in do_{verb}")
    check(not wrong, "each route is served by the verb its client uses", "; ".join(wrong))

    print("\n[4] both auth styles the clients use are accepted")
    auth = _handler_body(server_src, "_post_authorized") or server_src
    idx = server_src.find("def _post_authorized")
    auth = server_src[idx: idx + 700] if idx != -1 else ""
    check("Bearer" in auth, "the Bearer header the iPhone client sends is accepted", auth[:120])
    check('"token"' in auth or "'token'" in auth,
          "the ?token= query the HUD appends is accepted", auth[:120])
    iphone = (ROOT / "clients" / "iphone" / "index.html").read_text(encoding="utf-8")
    hud = (ROOT / "clients" / "hud" / "index.html").read_text(encoding="utf-8")
    check("Bearer" in iphone, "…and the iPhone client really sends it")
    check("token=" in hud, "…and the HUD really appends it")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
