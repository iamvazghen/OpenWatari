"""List host audio devices and show how Afon resolves speaker/headphone targets.

    uv run python bench/list_audio_devices.py
"""

from __future__ import annotations

import sys

# Force UTF-8 so Windows cp1252 console can't choke on device names like "Realtek(R)".
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from afon.edge.audio_devices import (  # noqa: E402
    default_input_index,
    default_output_index,
    find_output_device,
    list_devices,
)

devs = list_devices()
din, dout = default_input_index(), default_output_index()

print("=== audio devices ===")
for d in devs:
    tags = []
    if d.index == din:
        tags.append("DEFAULT-IN")
    if d.index == dout:
        tags.append("DEFAULT-OUT")
    flag = (" [" + ",".join(tags) + "]") if tags else ""
    print(f"  {d.index:>2} | in {d.max_input_channels} out {d.max_output_channels} | {d.name}{flag}")

print("\n=== Afon output resolution ===")
for q in ("speakers", "headphones", "airpods"):
    dev = find_output_device(q)
    print(f"  '{q}' -> {dev.name if dev else 'NOT CONNECTED'}")
