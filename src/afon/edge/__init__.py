"""Edge: the local Pipecat voice pipeline (ears + mouth).

Mic -> wake word -> VAD -> STT -> BrainBridge -> TTS -> speaker.
The "LLM" slot is the BrainBridge processor; reasoning happens in the brain.
"""
