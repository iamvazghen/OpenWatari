"""Audio device discovery + selection (speakers <-> headphones, incl. AirPods).

Afon can play through whichever output device the owner prefers — built-in laptop
speakers or Bluetooth headphones. On Windows, Apple AirPods Pro Max appear as a
*generic* Bluetooth audio device once paired+connected (there is no Apple SDK):

  * "Headphones (AirPods Pro Max Stereo)"  -> A2DP profile: high-quality OUTPUT only.
  * "Headset (AirPods Pro Max Hands-Free)" -> HFP profile: bidirectional but
    telephone-grade (8/16 kHz), and selecting it forces the mic into HFP too.

So realistically: yes, Afon can play through the AirPods (A2DP). Using them as the
*mic* simultaneously drops the whole link to low-quality HFP — for now Afon keeps the
laptop mic for input and only routes OUTPUT to the headphones. Output switching is what
the voice command toggles.

This module is pure discovery/resolution (no pipeline deps) so it is unit-testable on
its own and reused by both the edge transport builder and the runtime switch command.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from afon.shared.paths import state_dir


def _com_init() -> None:
    """Initialise COM on this worker thread (Windows only, best-effort).

    PortAudio's Windows host APIs (WASAPI/WDM-KS) enumerate through COM, which must be initialised
    per-thread. asyncio.to_thread() hands the call a fresh pool thread with no COM, and PortAudio
    then SEGFAULTS the whole process — a native crash Python cannot catch, so it killed the edge
    outright and made the audio-watchdog bench look 'environmentally flaky'. ctypes keeps this
    dependency-free (COINIT_APARTMENTTHREADED = 0x2).
    """
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes

        ctypes.windll.ole32.CoInitializeEx(None, 0x2)
    except Exception:  # noqa: BLE001 — already-initialised or unavailable: nothing to do
        pass


# ponytail: ONE dedicated thread for every PortAudio call. Single-threaded because PortAudio
# enumeration is not safe to run concurrently from several threads either, and COM is initialised
# once here instead of at every call site.
_audio_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="portaudio",
                                 initializer=_com_init)


def run_audio_call(fn, *args, **kwargs):
    """Run a PortAudio-touching callable on the dedicated COM-initialised thread."""
    return _audio_pool.submit(fn, *args, **kwargs).result()

# Friendly aliases -> substrings to look for in a device's reported name (lowercased).
# Lets a voice command say "headphones" / "speakers" without knowing the exact OS label.
OUTPUT_ALIASES: dict[str, tuple[str, ...]] = {
    "headphones": ("airpod", "headphone", "headset", "buds", "bluetooth"),
    "headset": ("airpod", "headset", "headphone"),
    "airpods": ("airpod",),
    "speakers": ("speaker", "realtek", "internal", "laptop"),
    "laptop": ("speaker", "realtek", "internal", "laptop"),
    "speaker": ("speaker", "realtek", "internal", "laptop"),
}

# Where the chosen output preference is persisted so a (re)started worker honors the
# last voice command. Small JSON, user-home scoped.
PREF_PATH = state_dir() / "audio_pref.json"


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    max_input_channels: int
    max_output_channels: int

    @property
    def is_output(self) -> bool:
        return self.max_output_channels > 0

    @property
    def is_input(self) -> bool:
        return self.max_input_channels > 0


def list_devices() -> list[AudioDevice]:
    """Enumerate all host audio devices via PyAudio."""
    import pyaudio

    pa = pyaudio.PyAudio()
    try:
        out: list[AudioDevice] = []
        for i in range(pa.get_device_count()):
            d = pa.get_device_info_by_index(i)
            out.append(
                AudioDevice(
                    index=i,
                    name=str(d.get("name", f"device {i}")),
                    max_input_channels=int(d.get("maxInputChannels", 0)),
                    max_output_channels=int(d.get("maxOutputChannels", 0)),
                )
            )
        return out
    finally:
        pa.terminate()


def default_output_index() -> int | None:
    import pyaudio

    pa = pyaudio.PyAudio()
    try:
        return int(pa.get_default_output_device_info().get("index"))
    except Exception:
        return None
    finally:
        pa.terminate()


def default_input_index() -> int | None:
    import pyaudio

    pa = pyaudio.PyAudio()
    try:
        return int(pa.get_default_input_device_info().get("index"))
    except Exception:
        return None
    finally:
        pa.terminate()


def _expand_query(query: str) -> tuple[str, ...]:
    """Turn a user word into the set of substrings to match against device names."""
    q = query.strip().lower()
    if q in OUTPUT_ALIASES:
        return OUTPUT_ALIASES[q]
    return (q,)  # treat as a literal substring (e.g. an exact device-name fragment)


def find_output_device(query: str, devices: list[AudioDevice] | None = None) -> AudioDevice | None:
    """Resolve a query like 'headphones' / 'airpods' / 'speakers' to an OUTPUT device.

    Numeric query -> that exact device index. Prefers non-Hands-Free (A2DP) matches so
    we get high-quality playback rather than the telephone-grade HFP endpoint.
    """
    devs = devices if devices is not None else list_devices()
    outputs = [d for d in devs if d.is_output]

    if query.strip().isdigit():
        idx = int(query.strip())
        return next((d for d in outputs if d.index == idx), None)

    needles = _expand_query(query)
    matches = [d for d in outputs if any(n in d.name.lower() for n in needles)]
    if not matches:
        return None
    # Prefer a high-quality stereo endpoint over the "Hands-Free" HFP one.
    non_hfp = [d for d in matches if "hands-free" not in d.name.lower() and "headset" not in d.name.lower()]
    pool = non_hfp or matches
    # Among those, prefer the one with the most output channels (stereo > mono).
    return max(pool, key=lambda d: d.max_output_channels)


# Substrings that mark an OUTPUT device as a PRIVATE/headphone endpoint (AirPods etc.). Used by
# auto-routing: if such a device is connected to this laptop, prefer it over the open speakers.
_PRIVATE_OUTPUT_CUES = ("airpod", "headphone", "headset", "buds", "earphone", "bluetooth", "bt audio")

# ...but the built-in codec ALWAYS lists a "Headphones (Realtek ... SST)" jack even with nothing plugged
# in — a WDM-KS endpoint that name-matches "headphone" yet fails to open (-9999). Exclude internal-codec
# endpoints so auto-route only ever picks a genuinely removable headset (AirPods/BT/USB), else OS default.
_INTERNAL_OUTPUT_CUES = ("realtek", "hd audio", "high definition audio", "sst", "hdmi", "displayport", "nvidia")

# The generic words a private endpoint's NAME is allowed to consist of. A real removable device
# carries a product identity beyond these ("Headphones (AirPods <owner>)"); the built-in jack on
# this laptop enumerates as a bare "Headphones ()" — production 2026-07-29: auto-route picked it and
# Afon answered ~20 turns into an endpoint with nothing attached (the "hey afon and nothing
# comes" report). No internal-cue matched because the product name is EMPTY, so require identity.
_GENERIC_NAME_RE = None  # built lazily below (module import stays cheap)


def _has_product_identity(name: str) -> bool:
    """True if the device name contains anything beyond generic headphone words + punctuation."""
    global _GENERIC_NAME_RE
    import re
    if _GENERIC_NAME_RE is None:
        _GENERIC_NAME_RE = re.compile(
            r"headphones?|headsets?|earphones?|earbuds|buds|airpods?|bluetooth|bt audio|stereo|hands.?free")
    core = _GENERIC_NAME_RE.sub("", name.lower())
    return bool(re.sub(r"[()\s\-_.,]+", "", core))


def prefer_private_output(devices: list[AudioDevice] | None = None) -> AudioDevice | None:
    """Return a connected private/headphone OUTPUT device (e.g. AirPods Pro Max) if one exists.

    This is the "auto-route to headphones" rule: when AirPods are connected to the laptop, Afon
    should play through them automatically (and barge-in then auto-enables via device_profile).
    Prefers a high-quality A2DP stereo endpoint over the telephone-grade Hands-Free one.
    """
    devs = devices if devices is not None else list_devices()
    outputs = [d for d in devs if d.is_output]
    matches = [
        d for d in outputs
        if any(c in d.name.lower() for c in _PRIVATE_OUTPUT_CUES)
        and not any(c in d.name.lower() for c in _INTERNAL_OUTPUT_CUES)  # skip the built-in headphone jack
        and _has_product_identity(d.name)  # a bare "Headphones ()" is the empty jack, not a device
    ]
    if not matches:
        return None
    non_hfp = [d for d in matches if "hands-free" not in d.name.lower() and "headset" not in d.name.lower()]
    pool = non_hfp or matches
    return max(pool, key=lambda d: d.max_output_channels)


# A genuine, RELIABLE headset mic we SHOULD listen through when it's plugged in (wired / USB headset).
# EXCLUDES Bluetooth "Hands-Free"/AirPods (HFP is telephone-grade and drops the stream) and the built-in
# codec array (that's the reliable default, not a headset). So this fires only for a real plugged-in
# headset mic; AirPods intentionally fall through to the built-in mic (owner's "smart per-device" choice).
_HEADSET_INPUT_CUES = ("headset", "headphone")
_HEADSET_INPUT_EXCLUDE = ("airpod", "hands-free", "handsfree", "hands free", "bluetooth", "bt audio",
                          "realtek", "intel", "hd audio", "high definition audio", "microphone array")


def find_headset_input(devices: list[AudioDevice] | None = None) -> AudioDevice | None:
    """A reliable wired/USB headset INPUT device if one is connected, else None (→ use the built-in mic)."""
    devs = devices if devices is not None else list_devices()
    for d in devs:
        if not d.is_input:
            continue
        n = d.name.lower()
        if any(c in n for c in _HEADSET_INPUT_CUES) and not any(x in n for x in _HEADSET_INPUT_EXCLUDE):
            return d
    return None


def find_input_device(query: str, devices: list[AudioDevice] | None = None) -> AudioDevice | None:
    devs = devices if devices is not None else list_devices()
    inputs = [d for d in devs if d.is_input]
    if query.strip().isdigit():
        idx = int(query.strip())
        return next((d for d in inputs if d.index == idx), None)
    needles = _expand_query(query)
    matches = [d for d in inputs if any(n in d.name.lower() for n in needles)]
    return matches[0] if matches else None


def route_should_change(
    bound_output_name: str | None, currently_private: bool, auto_route_headphones: bool = True
) -> str | None:
    """Return a reason to re-resolve the OUTPUT route (→ restart the edge), else None.

    Two runtime device changes, one enumeration:
      * the bound output vanished  → headphones DISCONNECTED (fall back to speakers), or
      * a private endpoint appeared while we're on a shared one → headphones CONNECTED (switch to them).
    Lets the edge follow AirPods connect/disconnect live. Enumerates PyAudio, so call it off the event
    loop (the watchdog runs it in a thread — BT device init blocks for seconds).
    """
    devs = list_devices()
    outputs = [d for d in devs if d.is_output]
    if bound_output_name:
        needle = bound_output_name.split("(")[0].strip().lower()
        if needle and not any(needle in d.name.lower() for d in outputs):
            return f"output '{bound_output_name}' disconnected"
    if auto_route_headphones and not currently_private and prefer_private_output(devs) is not None:
        return "headphones connected"
    return None


# ---- persisted preference (so a voice command survives a worker restart) ----------------

def save_output_preference(query: str) -> None:
    PREF_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREF_PATH.write_text(json.dumps({"output": query}), encoding="utf-8")


def load_output_preference() -> str | None:
    try:
        return json.loads(PREF_PATH.read_text(encoding="utf-8")).get("output")
    except Exception:
        return None


def resolve_output_index(
    preferred: str | None, auto_route_headphones: bool = True
) -> tuple[int | None, str]:
    """Pick an output device index given an optional preference (config or saved pref).

    Returns (index_or_None, human_label). None index means "use the OS default".

    With no explicit preference (or pref == 'auto') and ``auto_route_headphones`` on, Afon
    auto-routes to a connected private endpoint (AirPods Pro Max / headphones) when one is
    present — "if they're connected, send everything to my headphones" — otherwise the OS default.
    An explicit preference ('speakers', a device name, an index) always wins over auto-routing.
    """
    pref = preferred or load_output_preference()
    if not pref or pref.strip().lower() == "auto":
        if auto_route_headphones:
            priv = prefer_private_output()
            if priv is not None:
                return priv.index, f"{priv.name} (auto-routed to headphones)"
        idx = default_output_index()
        devs = list_devices()
        label = next((d.name for d in devs if d.index == idx), "system default")
        return idx, f"{label} (default)"
    dev = find_output_device(pref)
    if dev is None:
        return default_output_index(), f"'{pref}' not found — using system default"
    return dev.index, dev.name
