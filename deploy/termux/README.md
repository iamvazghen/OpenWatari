# Afon edge-lite for Android (Termux) — TODO 8.5

A stripped voice edge for a phone: **push-to-talk → mic → speech-to-text → brain WS → phone TTS**,
reusing the exact same brain protocol as the laptop edge and glasses. It registers as
`device_id="android"`, so the brain's Phase-6 routing already treats it as a phone speaker
(phone-speaker output, no barge-in) with no server-side changes.

## What it is (and isn't)

| Full laptop edge | Android edge-lite |
|---|---|
| Wake word (onnxruntime) | **Push-to-talk** (keypress / Termux widget) |
| VAD + barge-in | none (speak whole reply, then listen) |
| Local Whisper STT | **cloud STT** (Groq Whisper) via `termux-microphone-record` |
| Local Piper TTS | **`termux-tts-speak`** (phone engine) |
| Pipecat pipeline | one small `EdgeLite` loop over `BrainClient` |

These are deliberate simplifications for a battery-powered phone — see the header of
`src/afon/edge/edge_lite.py`. Everything (record / transcribe / speak / client) is injectable, so
the loop is unit-tested off-device in `bench/test_edge_lite.py`.

## Install (on the phone)

1. Install **Termux** and **Termux:API** (both from F-Droid — the Play Store builds are outdated).
2. In Termux:
   ```bash
   export AFON_GIT_URL=<private repo URL>
   bash deploy/termux/install.sh
   ```
3. Put secrets in `$HOME/.afon.env`:
   ```
   AFON_BRAIN_WS_URL=wss://<brain-host>:8765
   AFON_API_AUTH_TOKEN=<token>
   AFON_GROQ_API_KEY=<groq key>
   ```
4. Grant Termux:API the **Microphone** permission (Android Settings → Apps → Termux:API).

## Run

```bash
bash deploy/termux/run.sh
```

Press **Enter** to record ~6 s, `q` to quit. The reply is spoken through the phone.

## Runtime acceptance (needs a real device — not covered by the offline suite)

- [ ] `termux-microphone-record` produces a non-empty wav (mic permission granted).
- [ ] STT returns text for a spoken sentence.
- [ ] The brain answers and `termux-tts-speak` speaks it.
- [ ] The link survives a brain restart (BrainClient reconnects — same as every device).
- [ ] `device_id="android"` shows up in the brain's session log / `/metrics`.
