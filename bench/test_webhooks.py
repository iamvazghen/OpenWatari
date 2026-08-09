"""C2 — inbound integration webhooks: a correctly HMAC-signed payload feeds WORLD.note_event; a bad
or missing signature is rejected and the world-model never ingests it. Hermetic (no HTTP server)."""
import hashlib
import hmac
import json

from afon.brain import webhooks
from afon.config import settings

_ok = 0
_fail = 0


def check(cond, label):
    global _ok, _fail
    if cond:
        _ok += 1
    else:
        _fail += 1
        print(f"  FAIL: {label}")


class _FakeWorld:
    def __init__(self):
        self.events = []

    def note_event(self, text, *, ttl_hours=24.0, now=None):
        self.events.append((text, ttl_hours))


SECRET = "test-webhook-secret-123"
settings.webhook_secret = SECRET


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


# --- 1) a correctly-signed Stripe payout -> 200 + one world-model event ----------------------
w = _FakeWorld()
body = json.dumps({"type": "payout.paid",
                   "data": {"object": {"amount": 42000, "currency": "eur"}}}).encode()
status, msg = webhooks.handle_webhook("stripe", body, sign(body), world=w)
check(status == 200, f"signed stripe webhook accepted (got {status})")
check(len(w.events) == 1, "exactly one world-model event recorded")
check("Stripe" in w.events[0][0] and "420" in w.events[0][0], f"stripe summary has the amount ({w.events[0][0]!r})")

# --- 2) a tampered body (signature no longer matches) -> 401, NOTHING recorded ---------------
w2 = _FakeWorld()
good_sig = sign(body)
tampered = body.replace(b"42000", b"99999")
status2, _ = webhooks.handle_webhook("stripe", tampered, good_sig, world=w2)
check(status2 == 401, f"tampered body rejected (got {status2})")
check(w2.events == [], "a bad signature records NO event")

# --- 3) missing signature -> 401 -------------------------------------------------------------
w3 = _FakeWorld()
status3, _ = webhooks.handle_webhook("github", body, None, world=w3)
check(status3 == 401 and w3.events == [], "missing signature rejected, nothing recorded")

# --- 4) unknown source -> 404 ----------------------------------------------------------------
status4, _ = webhooks.handle_webhook("evil", body, sign(body), world=_FakeWorld())
check(status4 == 404, f"unknown source is 404 (got {status4})")

# --- 5) a GitHub PR event summarises usefully ------------------------------------------------
w5 = _FakeWorld()
gh = json.dumps({"action": "opened", "pull_request": {"number": 7},
                 "repository": {"full_name": "iamvazghen/party-map-app"}}).encode()
status5, _ = webhooks.handle_webhook("github", gh, sign(gh), world=w5)
check(status5 == 200 and "PR opened" in w5.events[0][0] and "party-map-app" in w5.events[0][0],
      f"github PR summarised ({w5.events[0][0] if w5.events else None!r})")

# --- 6) verify() is constant-time-compare correct on the happy + sad path --------------------
check(webhooks.verify(SECRET, body, sign(body)) is True, "verify accepts a correct signature")
check(webhooks.verify(SECRET, body, "sha256=deadbeef") is False, "verify rejects a wrong signature")
check(webhooks.verify(None, body, sign(body)) is False, "verify rejects when no secret configured")

print(f"=== {_ok}/{_ok + _fail} checks passed ===")
import sys
sys.exit(1 if _fail else 0)
