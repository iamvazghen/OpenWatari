"""C3 hermetic test — affect → ElevenLabs voice_settings map (no network).

The map is the tuning knob for mood-adaptive prosody. Assert valid ranges + the DIRECTION that matters:
stressed/low → steadier (higher stability) and not faster than neutral; upbeat → livelier (more style,
a touch quicker). Exact values are calibrated by ear (the 1 live listen-check), so we test properties.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from afon.brain.affect import Affect, affect_to_voice, infer_affect  # noqa: E402

_ok = _fail = 0


def check(cond, label):
    global _ok, _fail
    if cond:
        _ok += 1
    else:
        _fail += 1
        print(f"  FAIL: {label}")


neutral = affect_to_voice(Affect())
check(neutral == {"stability": 0.5, "style": 0.0, "speed": 1.0}, f"neutral = defaults (got {neutral})")

# every mood returns valid ElevenLabs ranges
for name, a in {
    "stress": Affect(stress=True), "tired": Affect(tired=True), "low": Affect(low=True),
    "upbeat": Affect(upbeat=True), "terse": Affect(terse=True),
}.items():
    v = affect_to_voice(a)
    check(0.0 <= v["stability"] <= 1.0, f"{name}: stability in [0,1] (got {v['stability']})")
    check(0.0 <= v["style"] <= 1.0, f"{name}: style in [0,1] (got {v['style']})")
    check(0.7 <= v["speed"] <= 1.2, f"{name}: speed in ElevenLabs range (got {v['speed']})")

# direction: stressed + low are steadier than neutral and NOT faster (calm, unhurried)
for mood in (Affect(stress=True), Affect(low=True)):
    v = affect_to_voice(mood)
    check(v["stability"] > neutral["stability"], f"{mood.tags()}: steadier than neutral")
    check(v["speed"] <= neutral["speed"], f"{mood.tags()}: not faster than neutral")

# direction: upbeat is livelier — more style, a touch quicker
up = affect_to_voice(Affect(upbeat=True))
check(up["style"] > neutral["style"] and up["speed"] > neutral["speed"], "upbeat is livelier")

# priority: emotional care wins when several flags are set (low beats terse/upbeat)
both = affect_to_voice(Affect(low=True, terse=True, upbeat=True))
check(both == affect_to_voice(Affect(low=True)), "low takes priority over terse/upbeat")

# end-to-end shape: a stressed owner utterance yields the calm profile
stressed = affect_to_voice(infer_affect("this is still broken and I'm so frustrated"))
check(stressed["stability"] >= 0.7, f"a frustrated message -> steady voice (got {stressed})")

print(f"=== {_ok}/{_ok + _fail} checks passed ===")
sys.exit(1 if _fail else 0)
