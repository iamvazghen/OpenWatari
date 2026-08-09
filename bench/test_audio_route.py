"""Auto-route regression: the built-in Realtek headphone JACK must NOT masquerade as connected headphones.

Repro of the 2026-07-24 -9999 outage: with AirPods gone, `prefer_private_output` grabbed
'Headphones 1 (Realtek HD Audio 2nd output with SST)' — an always-listed WDM-KS jack that fails to
open — instead of falling through to the OS default speakers.
"""
from afon.edge.audio_devices import AudioDevice, prefer_private_output, find_headset_input

_ok = _fail = 0


def check(cond, label):
    global _ok, _fail
    if cond:
        _ok += 1
    else:
        _fail += 1
        print(f"  FAIL: {label}")


def out(idx, name):
    return AudioDevice(index=idx, name=name, max_input_channels=0, max_output_channels=2)


# 1) AirPods gone: only the internal Realtek jack + speakers exist -> auto-route picks NOTHING (fall to default)
no_bt = [out(3, "Speakers (2- Realtek(R) Audio)"), out(14, "Headphones 1 (Realtek HD Audio 2nd output with SST)")]
check(prefer_private_output(no_bt) is None, "internal Realtek headphone jack is NOT auto-routed as private")

# 2) AirPods present: they win over the internal jack
with_bt = no_bt + [out(4, "Headphones (AirPods Vazghen Stereo)")]
picked = prefer_private_output(with_bt)
check(picked is not None and picked.index == 4, "AirPods still auto-route when connected")

# 3) A genuine external USB headset (no internal-codec marker) still counts
usb = [out(3, "Speakers (2- Realtek(R) Audio)"), out(9, "Headphones (USB Audio Device)")]
picked = prefer_private_output(usb)
check(picked is not None and picked.index == 9, "external USB headphones still auto-route")

# ---- smart per-device MIC routing (owner's choice: reliable headset mic, AirPods -> built-in) --------
def inp(idx, name):
    return AudioDevice(index=idx, name=name, max_input_channels=1, max_output_channels=0)


# 4) a wired/USB headset mic IS routed to (walk away from the laptop and still be heard)
usb_hs = [inp(1, "Microphone Array (Intel Smart Sound)"), inp(5, "Headset Microphone (USB Audio Device)")]
picked = find_headset_input(usb_hs)
check(picked is not None and picked.index == 5, "a wired/USB headset mic is auto-selected for input")

# 5) AirPods Hands-Free (HFP) is NOT routed to — telephone-grade + drops the stream, use built-in
airpods = [inp(1, "Microphone Array (Intel Smart Sound)"), inp(2, "Headset (AirPods Vazghen Hands-Free)")]
check(find_headset_input(airpods) is None, "AirPods HFP mic is NOT auto-selected (falls back to built-in)")

# 6) plain built-in array alone -> no headset (use the default/pinned built-in)
check(find_headset_input([inp(1, "Microphone Array (2- Intel Smart Sound)")]) is None,
      "the built-in Realtek/Intel array is not treated as a headset mic")

print(f"=== {_ok}/{_ok + _fail} checks passed ===")
import sys
sys.exit(1 if _fail else 0)
