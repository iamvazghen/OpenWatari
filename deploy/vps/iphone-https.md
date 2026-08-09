# iPhone mic client over HTTPS (TODO 8.2)

The web client at `clients/iphone/index.html` captures the phone mic with `getUserMedia`, which the
browser **only** allows in a *secure context* (`https://` or `localhost`). The brain's HTTP sidecar
(`client_http_port`, default **8766**) speaks plain HTTP, so we put HTTPS in front of it with
`tailscale serve` — no certificates to manage, and it's only reachable from your own tailnet.

## One-time setup (on the brain host)

Port 443 on this tailnet already fronts the OpenClaw gateway, so the brain sidecar gets its **own**
HTTPS port (8443) — this keeps the iPhone page and its `/talk` POST on one origin:

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:8766

# Confirm:
tailscale serve status
#   https://<host>.<tailnet>.ts.net:8443  ->  http://127.0.0.1:8766
```

Then on the iPhone (signed into the same tailnet via the Tailscale app), open the **live URL**:

```
https://openclaw-vps.tailc29aaf.ts.net:8443/iphone/
```

Add it to the Home Screen ("Add to Home Screen") for a full-screen, app-like button.

## Why this is safe

- **Tailnet-only.** `tailscale serve` (not `funnel`) never exposes the port to the public internet —
  only devices in your tailnet can reach it. Do **not** use `tailscale funnel` here unless you
  intend a public endpoint.
- **Token-gated.** The sidecar still requires `Authorization: Bearer <api_auth_token>`. The brain
  injects that token into the page at serve time (`server.py` rewrites `</head>`), so the phone
  authenticates automatically without you pasting a secret into the browser.
- **No stale UI.** The `/iphone` route sends no-cache headers so Safari always loads the current page.

## How a turn flows

```
mic (getUserMedia) → MediaRecorder (audio/mp4) → POST /talk  (Bearer token)
                                                     │
                                    handle_voice_request: Deepgram STT → agent turn → TTS (MP3)
                                                     │
                          ← audio/mpeg (played inline)  + X-Afon-Reply / X-Afon-Transcript headers
```

If TTS synthesis is unavailable the sidecar returns `{"reply","transcript"}` JSON instead, and the
page falls back to the browser's own `speechSynthesis` — so the client always speaks something.

## Runtime acceptance (needs the iPhone — not covered by the offline suite)

- [ ] `https://…ts.net/iphone/` loads with a valid cert (no warning) and shows the mic button.
- [ ] First tap prompts for microphone permission; granting it starts the pulse animation.
- [ ] A spoken sentence returns a transcript + Afon's reply, and the MP3 plays.
- [ ] With the brain's TTS disabled, the JSON fallback still shows text and speaks via the browser.
- [ ] An invalid/missing token yields a clear "Unauthorized" message, not a silent failure.
