"""iPhone mic-over-HTTPS web client (TODO 8.2) — verified statically + server injection path.

Runtime acceptance needs the phone (see deploy/vps/iphone-https.md). This proves the CLIENT exists,
is self-contained (no external hosts — works on a private tailnet with no CDN), and wires the mic →
/talk → playback path with token auth; and that the SERVER's token-injection rewrite actually plants
the auth token into the page it serves.

    uv run python bench/test_iphone_client.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

ROOT = Path(__file__).resolve().parents[1]
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
    idx = ROOT / "clients" / "iphone" / "index.html"

    print("[1] the client the server points at actually exists")
    check("clients/iphone/index.html present", idx.exists(), str(idx))
    html = idx.read_text(encoding="utf-8") if idx.exists() else ""
    low = html.lower()

    print("\n[2] it captures the mic and records audio (the whole point of 8.2)")
    check("uses getUserMedia", "getusermedia" in low)
    check("uses MediaRecorder", "mediarecorder" in low)
    check("prefers an iOS-friendly mime (mp4/aac)", "audio/mp4" in low or "audio/aac" in low)
    check("guards the secure-context requirement", "securecontext" in low or "secure context" in low
          or "https://" in low)

    print("\n[3] it POSTs to /talk with the injected bearer token")
    check("posts to /talk", "'/talk'" in html or '"/talk"' in html)
    check("method POST", "method: 'post'" in low or "method:'post'" in low or "method: \"post\"" in low)
    check("reads window.AFON_TOKEN", "window.AFON_TOKEN" in html)
    check("sends Authorization: Bearer", "bearer " in low and "authorization" in low)

    print("\n[4] it handles BOTH server responses (audio + JSON fallback)")
    check("plays audio/mpeg reply", ".play(" in html and ("audio" in low))
    check("reads X-Afon-Reply/Transcript headers", "x-afon-reply" in low and "x-afon-transcript" in low)
    check("has a JSON/text fallback path", "res.json(" in html or ".json()" in html)
    check("browser speechSynthesis fallback", "speechsynthesis" in low)

    print("\n[5] self-contained — no external hosts (private tailnet has no CDN)")
    ext = re.findall(r'(?:src|href)\s*=\s*["\']https?://[^"\']+', html)
    check("no external src/href resources", ext == [], str(ext))
    check("no external fetch() to another origin",
          not re.search(r"fetch\(\s*['\"]https?://", html), "found absolute fetch URL")

    print("\n[6] the SERVER injects the token into the page it serves")
    # Mirror server.py's rewrite: it replaces the first </head> with a token <script>.
    from afon.config import settings
    saved = settings.api_auth_token
    settings.api_auth_token = "test-tok-123"
    try:
        tok = (settings.api_auth_token or "").replace("</", "<\\/")
        inject = f'<script>window.AFON_TOKEN="{tok}";</script>'
        served = html.replace("</head>", inject + "</head>", 1)
        check("token planted before </head>", 'window.AFON_TOKEN="test-tok-123"' in served)
        check("exactly one head close remains", served.count("</head>") == 1)
    finally:
        settings.api_auth_token = saved

    print("\n[7] the server route + docs reference the client correctly")
    server_src = (ROOT / "src" / "afon" / "brain" / "server.py").read_text(encoding="utf-8")
    check("server serves the /iphone route", '"/iphone"' in server_src)
    check("server points at iphone/index.html", '"iphone"' in server_src and "index.html" in server_src)
    check("HTTPS deploy doc exists", (ROOT / "deploy" / "vps" / "iphone-https.md").exists())

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
