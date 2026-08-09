"""Smart output-device profiling → automatic barge-in decision.

Barge-in (interrupting Afon mid-sentence by speaking) is only safe when the mic CANNOT
re-hear Afon's own TTS. On open laptop/desktop speakers it can, so VAD would flag the
playback as "user speech" and Afon would interrupt himself — barge-in must stay OFF.
On a **private** endpoint (headphones / AirPods / earbuds / smart-glasses / a phone with
earbuds) the speaker is in/at your ear and the mic is isolated, so barge-in is safe and
should turn ON by itself.

This module is the identifier that decides that — automatically — from whatever endpoint is
actually in use, across devices:

  * Local PC: classify the resolved output device NAME (PyAudio) — Bluetooth/AirPods/headset
    → private; Realtek/internal/laptop speakers → shared.
  * Mentra glasses / iPhone / phone clients: the local device name says nothing about how YOU
    hear Afon, so the remote client DECLARES a hint ('glasses', 'phone-headphones',
    'phone-speaker', …) which we trust over name-sniffing.

Pure logic, no audio/pipeline deps, so it is unit-testable and reused by the edge builder.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OutputKind(str, Enum):
    headphones = "headphones"            # wired/Bluetooth headphones, AirPods, earbuds — PRIVATE
    glasses = "glasses"                  # MentraOS smart-glasses (on-device AEC) — PRIVATE
    phone_headphones = "phone_headphones"  # iPhone/Android with earbuds connected — PRIVATE
    phone_speaker = "phone_speaker"      # phone loudspeaker (iPhone/Android) — SHARED
    speakers = "speakers"                # laptop/desktop open speakers (Windows/macOS/Linux) — SHARED
    unknown = "unknown"

    @property
    def is_private(self) -> bool:
        """True when the listener's ear is isolated, so the mic won't re-hear the TTS."""
        return self in {OutputKind.headphones, OutputKind.glasses, OutputKind.phone_headphones}


# Explicit hints a remote client can send (case-insensitive). These win over name-sniffing
# because only the device itself knows how the user is actually listening.
_HINT_MAP: dict[str, OutputKind] = {
    "glasses": OutputKind.glasses,
    "mentra": OutputKind.glasses,
    "mentraos": OutputKind.glasses,
    "phone-headphones": OutputKind.phone_headphones,
    "phone_headphones": OutputKind.phone_headphones,
    "iphone-headphones": OutputKind.phone_headphones,
    "android-headphones": OutputKind.phone_headphones,
    "android_headphones": OutputKind.phone_headphones,
    "phone-speaker": OutputKind.phone_speaker,
    "phone_speaker": OutputKind.phone_speaker,
    "iphone": OutputKind.phone_speaker,
    "android": OutputKind.phone_speaker,
    "android-speaker": OutputKind.phone_speaker,
    "phone": OutputKind.phone_speaker,
    "headphones": OutputKind.headphones,
    "airpods": OutputKind.headphones,
    "earbuds": OutputKind.headphones,
    "headset": OutputKind.headphones,
    "speakers": OutputKind.speakers,
    "speaker": OutputKind.speakers,
    "laptop": OutputKind.speakers,
    "mac": OutputKind.speakers,
    "macos": OutputKind.speakers,
    "macbook": OutputKind.speakers,
}

# Substrings in a Windows output-device NAME → kind. Private cues take priority over speaker
# cues so "Headphones (AirPods Pro Max Stereo)" classifies private even though it's an output.
_PRIVATE_NAME_CUES = ("airpod", "headphone", "headset", "buds", "earphone", "bluetooth", "bt audio")
_SPEAKER_NAME_CUES = ("speaker", "realtek", "internal", "laptop", "display audio", "monitor", "hdmi")


def classify_hint(hint: str | None) -> OutputKind | None:
    if not hint:
        return None
    return _HINT_MAP.get(hint.strip().lower())


def classify_output_name(name: str | None) -> OutputKind:
    """Best-effort classification of a local output device by its reported name."""
    if not name:
        return OutputKind.unknown
    n = name.lower()
    if any(c in n for c in _PRIVATE_NAME_CUES):
        return OutputKind.headphones
    if any(c in n for c in _SPEAKER_NAME_CUES):
        return OutputKind.speakers
    return OutputKind.unknown


def resolve_barge_in(
    mode: str,
    output_name: str | None = None,
    device_hint: str | None = None,
    legacy_enabled: bool = False,
    aec_active: bool = False,
) -> tuple[bool, OutputKind, str]:
    """Decide whether barge-in is on for the live endpoint.

    Returns ``(enabled, kind, reason)``. ``mode`` is 'auto' | 'on' | 'off':
      * 'on'  — forced full-duplex (you've set up AEC / know it's safe).
      * 'off' — forced half-duplex.
      * 'auto'— enable iff the endpoint is private OR a real echo-canceller is running (``aec_active``,
                Phase 1.2) — which removes Afon's own TTS from the mic so OPEN speakers become safe for
                barge-in too. A remote ``device_hint`` wins over the local ``output_name``; if neither
                classifies, fall back to ``legacy_enabled``.
    """
    m = (mode or "auto").strip().lower()
    if m == "on":
        return True, OutputKind.headphones, "forced on (barge_in_mode=on)"
    if m == "off":
        return False, OutputKind.speakers, "forced off (barge_in_mode=off)"

    kind = classify_hint(device_hint) or classify_output_name(output_name)
    if kind == OutputKind.unknown:
        # Can't tell — honour the legacy switch, defaulting to the speaker-safe OFF.
        if legacy_enabled:
            return True, kind, "device unknown — barge_in_enabled=true override"
        return False, kind, "device unknown — staying safe (half-duplex)"

    if kind.is_private:
        return True, kind, f"private endpoint ({kind.value}) — barge-in auto-enabled"
    # Shared speaker + a real echo-canceller on the mic (Phase 1.2) → full-duplex is now safe.
    if aec_active:
        return True, kind, f"shared endpoint ({kind.value}) but AEC active — full-duplex enabled"
    return False, kind, f"shared endpoint ({kind.value}) — barge-in off (speaker-safe)"


# ---- Phase 6: the supported devices, made explicit --------------------------------------
# Each is "enabled": Afon serves it from the same brain, classifies its endpoint, and routes
# barge-in/audio accordingly. A phone/glasses client connects over the edge<->brain protocol and
# declares its `device_id`; a laptop / Mac is a local host running the full pipeline (the voice
# edge is pure Python + PyAudio, so Windows, macOS and Linux all run it natively).
@dataclass(frozen=True)
class DeviceRoute:
    device_id: str
    label: str
    kind: OutputKind
    is_local: bool          # True for a local host (Windows laptop / Mac / Linux) running the edge
    barge_in: bool          # derived: private endpoint -> barge-in on


SUPPORTED_DEVICES: dict[str, dict] = {
    "laptop": {"label": "This host laptop (Windows/Linux)", "kind": OutputKind.speakers, "is_local": True},
    "mac": {"label": "Mac (macOS)", "kind": OutputKind.speakers, "is_local": True},
    "iphone": {"label": "iPhone", "kind": OutputKind.phone_speaker, "is_local": False},
    "android": {"label": "Android phone", "kind": OutputKind.phone_speaker, "is_local": False},
    "airpods": {"label": "AirPods / Bluetooth headphones", "kind": OutputKind.headphones, "is_local": False},
    "mentra": {"label": "Mentra OS glasses", "kind": OutputKind.glasses, "is_local": False},
}

# Aliases -> canonical device id.
_DEVICE_ALIASES = {
    "host": "laptop", "pc": "laptop", "desktop": "laptop", "laptop": "laptop",
    "windows": "laptop", "linux": "laptop",
    "mac": "mac", "macos": "mac", "macbook": "mac", "osx": "mac", "darwin": "mac",
    "iphone": "iphone", "ios": "iphone",
    "android": "android", "samsung": "android", "pixel": "android", "termux": "android",
    "phone": "iphone",
    "airpods": "airpods", "airpods-pro-max": "airpods", "headphones": "airpods",
    "mentra": "mentra", "mentraos": "mentra", "glasses": "mentra",
}


def resolve_device_route(device: str | None, headphones_connected: bool = False) -> DeviceRoute:
    """Map one of the four devices (id/alias) to its route + barge-in decision.

    ``headphones_connected`` implements the user's rule: if headphones/AirPods are connected to
    ANY base device (laptop, Mac, iPhone, Android), route everything to the headphones (private) and
    turn barge-in on — regardless of the base device. Unknown ids fall back to the laptop/host.
    """
    canon = _DEVICE_ALIASES.get((device or "laptop").strip().lower(), "laptop")
    spec = SUPPORTED_DEVICES[canon]
    kind: OutputKind = spec["kind"]
    label = spec["label"]
    if headphones_connected and kind in {OutputKind.speakers, OutputKind.phone_speaker}:
        kind = OutputKind.headphones
        label = f"{label} → headphones"
    return DeviceRoute(
        device_id=canon, label=label, kind=kind,
        is_local=spec["is_local"], barge_in=kind.is_private,
    )
