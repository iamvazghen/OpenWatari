"""Phase 6 — multi-device routing + protocol (offline).

Verifies the four supported devices are enabled and route correctly (Mentra glasses, iPhone,
AirPods Pro Max with the auto-route-to-headphones rule, and this host laptop), and that the
edge<->brain protocol carries the device declaration.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

passed = failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    mark = "[PASS]" if ok else "[FAIL]"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  {mark} {name}" + (f" — {detail}" if detail and not ok else ""))


def main() -> None:
    from afon.edge.device_profile import (
        SUPPORTED_DEVICES,
        OutputKind,
        resolve_device_route,
    )

    print("[1] all supported devices are enabled (Windows/Linux laptop, Mac, iPhone, Android, AirPods, Mentra)")
    for dev in ("laptop", "mac", "iphone", "android", "airpods", "mentra"):
        check(f"{dev} is registered", dev in SUPPORTED_DEVICES)

    print("\n[2] routing + barge-in per device")
    laptop = resolve_device_route("laptop")
    check("laptop -> speakers, local, barge-in OFF",
          laptop.kind == OutputKind.speakers and laptop.is_local and laptop.barge_in is False)

    mac = resolve_device_route("mac")
    check("Mac (macOS) -> speakers, local host, barge-in OFF",
          mac.kind == OutputKind.speakers and mac.is_local and mac.barge_in is False)

    iphone = resolve_device_route("iphone")
    check("iPhone -> phone speaker, remote, barge-in OFF",
          iphone.kind == OutputKind.phone_speaker and not iphone.is_local and iphone.barge_in is False)

    android = resolve_device_route("android")
    check("Android -> phone speaker, remote, barge-in OFF",
          android.kind == OutputKind.phone_speaker and not android.is_local and android.barge_in is False)

    airpods = resolve_device_route("airpods")
    check("AirPods -> headphones (private), barge-in ON",
          airpods.kind == OutputKind.headphones and airpods.barge_in is True)

    mentra = resolve_device_route("mentra")
    check("Mentra glasses -> glasses (private), barge-in ON",
          mentra.kind == OutputKind.glasses and mentra.barge_in is True)

    print("\n[3] headphones auto-route: connected to ANY base device -> headphones + barge-in ON")
    lap_hp = resolve_device_route("laptop", headphones_connected=True)
    check("laptop + headphones -> headphones, barge-in ON",
          lap_hp.kind == OutputKind.headphones and lap_hp.barge_in is True, str(lap_hp))
    iph_hp = resolve_device_route("iphone", headphones_connected=True)
    check("iPhone + headphones -> headphones, barge-in ON",
          iph_hp.kind == OutputKind.headphones and iph_hp.barge_in is True, str(iph_hp))
    and_hp = resolve_device_route("android", headphones_connected=True)
    check("Android + headphones -> headphones, barge-in ON",
          and_hp.kind == OutputKind.headphones and and_hp.barge_in is True, str(and_hp))

    print("\n[4] aliases resolve")
    for alias, canon in (("glasses", "mentra"), ("phone", "iphone"), ("ios", "iphone"),
                         ("macbook", "mac"), ("darwin", "mac"), ("pixel", "android"),
                         ("samsung", "android"), ("termux", "android"),
                         ("headphones", "airpods"), ("pc", "laptop"), ("airpods-pro-max", "airpods")):
        check(f"'{alias}' -> {canon}", resolve_device_route(alias).device_id == canon)
    check("unknown device falls back to laptop", resolve_device_route("toaster").device_id == "laptop")

    print("\n[5] laptop AirPods auto-route preference (prefer_private_output)")
    from afon.edge.audio_devices import AudioDevice, prefer_private_output

    devs = [
        AudioDevice(0, "Speakers (Realtek(R) Audio)", 0, 2),
        AudioDevice(1, "Headphones (AirPods Pro Max Stereo)", 0, 2),
        AudioDevice(2, "Headset (AirPods Pro Max Hands-Free)", 1, 1),
        AudioDevice(3, "Microphone (Realtek)", 2, 0),
    ]
    picked = prefer_private_output(devs)
    check("prefers AirPods over speakers when connected", picked is not None and "airpod" in picked.name.lower(),
          str(picked))
    check("prefers A2DP stereo over Hands-Free", picked is not None and "hands-free" not in picked.name.lower(),
          str(picked))
    no_hp = [AudioDevice(0, "Speakers (Realtek(R) Audio)", 0, 2)]
    check("no headphones -> None (falls back to default)", prefer_private_output(no_hp) is None)

    print("\n[6] edge<->brain protocol carries the device")
    from afon.shared.protocol import EdgeToBrain, Hello, Utterance  # noqa: F401

    h = Hello(session_id="s1", device_id="mentra", headphones_connected=False)
    check("Hello serialises device_id", h.model_dump()["device_id"] == "mentra")
    u = Utterance(session_id="s1", text="hi", ts_user_stop_ms=123, device_id="iphone")
    round_trip = Utterance.model_validate_json(u.model_dump_json())
    check("Utterance round-trips device_id", round_trip.device_id == "iphone")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
