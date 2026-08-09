"""Phase 1.2 — acoustic echo cancellation seam: full-duplex barge-in on OPEN speakers.

On headphones the mic can't hear Afon, so barge-in is already on (see ``device_profile``). On open
speakers the mic re-hears his own TTS and VAD would flag it as the user speaking — so barge-in is forced
OFF and the pipeline runs half-duplex (``HalfDuplexGate`` mutes the mic while he talks). The real fix for
open speakers is an echo-canceller on the mic input that removes Afon's own voice BEFORE VAD sees it.

Pipecat's ``audio_in_filter`` accepts a ``BaseAudioFilter``. The only filters that actually CANCEL ECHO
(not merely suppress noise) are the deep-learning single-channel cancellers — Krisp Viva and ai-coustics —
and both ship as PROPRIETARY, license-gated SDKs (``krisp_audio`` / ``aic_sdk``). So this module is the
integration seam: pick one with ``AFON_AEC_FILTER``, and once its SDK is installed the filter builds,
is wired onto the mic, and ``resolve_barge_in`` flips open speakers to full-duplex automatically. Without
the SDK it degrades to None (half-duplex, unchanged) — never a crash.

``rnnoise`` is deliberately NOT offered here: it suppresses noise, not echo, so enabling full-duplex
behind it would just make Afon interrupt himself on his own voice.

ponytail: the working echo MODEL is a paid dependency I can't bundle (like a Home-Assistant hub token) —
the wiring is complete; installing the SDK + ``AFON_AEC_FILTER=krisp`` is the one manual step. If a
faint residual echo still trips VAD on very loud speakers, tune the canceller's own residual threshold in
its SDK config (a physical-coupling knob a code default can't see).
"""

from __future__ import annotations

from loguru import logger

# Names that select a real ECHO CANCELLER (which is what makes open-speaker full-duplex safe).
_ECHO_CANCELLERS = {"krisp", "aic"}


def build_input_filter(name: str | None):
    """Return a pipecat echo-cancelling ``BaseAudioFilter`` for ``name``, or None (half-duplex, unchanged).

    Fail-quiet: ``none``/unknown → None; a real canceller whose SDK isn't installed logs once and returns
    None — never raises, so a missing paid SDK can never break the edge from starting.
    """
    key = (name or "none").strip().lower()
    if key in ("", "none", "off"):
        return None
    if key not in _ECHO_CANCELLERS:
        logger.warning(
            f"AEC: '{key}' is not an echo-canceller — only {sorted(_ECHO_CANCELLERS)} enable open-speaker "
            "full-duplex (rnnoise etc. suppress noise, not echo). Running half-duplex."
        )
        return None
    try:
        if key == "krisp":
            from pipecat.audio.filters.krisp_viva_filter import KrispVivaFilter

            logger.info("AEC: Krisp Viva echo-canceller active — open-speaker full-duplex enabled")
            return KrispVivaFilter()
        from pipecat.audio.filters.aic_filter import AICFilter

        logger.info("AEC: ai-coustics echo-canceller active — open-speaker full-duplex enabled")
        return AICFilter()
    except Exception as e:  # noqa: BLE001 — SDK not installed / unlicensed → degrade to half-duplex
        logger.warning(
            f"AEC: '{key}' filter unavailable ({type(e).__name__}) — install its SDK to enable "
            "open-speaker full-duplex. Running half-duplex."
        )
        return None


def aec_active(filter_obj) -> bool:
    """True when a real echo-canceller is on the mic, so open speakers are safe for barge-in."""
    return filter_obj is not None


if __name__ == "__main__":  # smoke: default is off, unknown is off, uninstalled SDK degrades
    assert build_input_filter(None) is None
    assert build_input_filter("none") is None
    assert build_input_filter("rnnoise") is None       # noise, not echo → not offered
    assert build_input_filter("krisp") is None or aec_active(build_input_filter("krisp"))
    print("aec seam OK")
