"""One-time Google OAuth — produces AFON_GOOGLE_REFRESH_TOKEN for Gmail + Calendar.

Prereqs (do these once):
  1. Go to https://console.cloud.google.com/ → create (or pick) a project.
  2. APIs & Services → Library → enable **Gmail API** and **Google Calendar API**.
  3. APIs & Services → OAuth consent screen → External → add yourself as a Test user.
  4. APIs & Services → Credentials → Create Credentials → OAuth client ID → **Web application**.
     Add this EXACT redirect URI:  http://127.0.0.1:8585/oauth2callback
     (or whatever you set AFON_GOOGLE_OAUTH_REDIRECT to).
  5. Put the client id + secret in .env:
        AFON_GOOGLE_CLIENT_ID=...
        AFON_GOOGLE_CLIENT_SECRET=...
  6. Run:  uv run python bench/google_login.py

It opens your browser, you grant Gmail + Calendar access, and it prints the refresh token. Paste
that into .env as AFON_GOOGLE_REFRESH_TOKEN — then Afon can read/send mail and your calendar.
"""

from __future__ import annotations

import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

sys.path.insert(0, "src")
from afon.brain.google import SCOPES, TOKEN_URL  # noqa: E402
from afon.config import settings  # noqa: E402

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_code: dict[str, str] = {}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _code["code"] = (params.get("code") or [""])[0]
        _code["error"] = (params.get("error") or [""])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        msg = "Google authorized — close this tab and return to the terminal."
        self.wfile.write(f"<html><body><h2>{msg}</h2></body></html>".encode())

    def log_message(self, *_):  # silence server logs
        pass


def main() -> None:
    if not (settings.google_client_id and settings.google_client_secret):
        print("Set AFON_GOOGLE_CLIENT_ID and AFON_GOOGLE_CLIENT_SECRET in .env first.")
        sys.exit(1)

    redirect = settings.google_oauth_redirect
    parsed = urllib.parse.urlparse(redirect)
    host, port = parsed.hostname or "127.0.0.1", parsed.port or 8585

    auth_url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": settings.google_client_id,
        "response_type": "code",
        "redirect_uri": redirect,
        "scope": " ".join(SCOPES),
        "access_type": "offline",      # required to get a refresh token
        "prompt": "consent",           # force a refresh token even on re-consent
    })

    server = HTTPServer((host, port), _Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    print(f"Opening your browser to authorize Gmail + Calendar… (listening on {redirect})")
    webbrowser.open(auth_url)

    for _ in range(180):
        if _code.get("code") or _code.get("error"):
            break
        time.sleep(1)

    if _code.get("error"):
        print(f"Authorization failed: {_code['error']}")
        sys.exit(1)
    if not _code.get("code"):
        print("Timed out waiting for authorization.")
        sys.exit(1)

    r = httpx.post(
        TOKEN_URL,
        data={
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "code": _code["code"],
            "redirect_uri": redirect,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    r.raise_for_status()
    token = r.json().get("refresh_token")
    if not token:
        print(f"No refresh token returned (did you use prompt=consent + access_type=offline?): {r.text}")
        sys.exit(1)
    print("\n" + "=" * 60)
    print("SUCCESS — paste this line into your .env:\n")
    print(f"AFON_GOOGLE_REFRESH_TOKEN={token}")
    print("=" * 60)


if __name__ == "__main__":
    main()
