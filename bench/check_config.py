"""Sanity-check that settings load from .env. Prints NO secrets."""

import sys

sys.path.insert(0, "src")
from afon.config import settings  # noqa: E402


def mask(v: str | None) -> str:
    return "set" if v else "MISSING"


print("stt_provider          :", settings.stt_provider.value)
print("tts_provider          :", settings.tts_provider.value)
print("llm_backend           :", settings.llm_backend.value)
print("llm_chain             :", settings.llm_chain)
print("brain host:port       :", f"{settings.brain_host}:{settings.brain_port}")
print("wake words            :", settings.wake_words_list, "enabled=", settings.wake_word_enabled)
print("vad / duplex          :", f"vad={settings.vad_enabled}", f"duplex={settings.duplex_mode}")
print("openclaw delegation   :", settings.openclaw_delegation_enabled)
print("openclaw gateway url   :", settings.openclaw_gateway_url, "router=", settings.openclaw_router_agent)
print("vault_path            :", settings.vault_path)
print("-- secrets present? (values not shown) --")
print("  elevenlabs_api_key  :", mask(settings.elevenlabs_api_key))
print("  elevenlabs_voice_id :", mask(settings.elevenlabs_voice_id))
print("  deepgram_api_key    :", mask(settings.deepgram_api_key))
print("  freellmapi_api_key  :", mask(settings.freellmapi_api_key))
print("  openclaw_token      :", mask(settings.openclaw_token))
print("  telegram_bot_token  :", mask(settings.telegram_bot_token))
