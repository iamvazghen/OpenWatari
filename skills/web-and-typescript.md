# The non-Python parts — TypeScript (glasses) and HTML/JS (phone client)

Most of you is Python, but two surfaces use other languages. When you self-improve those, work the
same way (branch → edit → commit), and prefer to keep logic in the Python brain — these are thin
bridges, not brains.

## Mentra glasses bridge — NOT PURSUED, and `glasses/` no longer exists
- Dropped for want of an SDK and an account; the half-finished TypeScript client was deleted. Do not
  offer to edit `glasses/` or describe a bridge that ships — there is no such directory.
- The mobile eye is the PHONE CAMERA instead, and it reuses the same `LLMClient.see` surface
  (`tools/camera.py` → `look_around` / `visual_presence`), so nothing has to change if a wearable
  ever does land.

## iPhone / web client — HTML + JS (`clients/iphone/`)
- A single self-contained `index.html`: Web Speech API for STT (`webkitSpeechRecognition`) with a
  text fallback, `speechSynthesis` for TTS, and a WebSocket to the brain.
- It speaks the same protocol: send `{type:"hello", session_id, device_id, headphones_connected}`,
  then `{type:"utterance", ...}`; handle `StreamEvent` lifecycle/tool/assistant frames.
- When you change the protocol in `shared/protocol.py`, update BOTH this client and the glasses
  bridge to match, or remote devices break. The brain server (`brain/server.py`) is the contract.

## Rule of thumb
A new capability belongs in the Python brain as a tool (so every device gets it at once), not in a
client. Touch the clients only for input/output/display concerns.
