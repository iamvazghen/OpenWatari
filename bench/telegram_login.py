"""One-time interactive Telegram login (creates the Telethon session file).

Run this ONCE so Afon's `check_telegram` tool can read your unread DMs. It signs you in as
your Telegram USER (the Bot API can't read DMs) and writes a session named
`AFON_TELEGRAM_SESSION` (default 'afon') next to where you run it. After that, the tool
works unattended.

    uv sync --extra channels
    uv run python bench/telegram_login.py

You'll need your api id + hash from https://my.telegram.org (set AFON_TELEGRAM_API_ID /
AFON_TELEGRAM_API_HASH in .env first), then enter your phone number and the code Telegram sends.
"""

from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, "src")

from afon.config import settings  # noqa: E402


async def main() -> None:
    if not (settings.telegram_api_id and settings.telegram_api_hash):
        print("Set AFON_TELEGRAM_API_ID and AFON_TELEGRAM_API_HASH in .env first "
              "(get them at https://my.telegram.org).")
        sys.exit(1)
    try:
        from telethon import TelegramClient
    except ImportError:
        print("Telethon isn't installed. Run:  uv sync --extra channels")
        sys.exit(1)

    client = TelegramClient(
        settings.telegram_session, settings.telegram_api_id, settings.telegram_api_hash
    )

    print("\n" + "=" * 64)
    print(" Telegram one-time login")
    print("=" * 64)
    print(" 1) Enter your PHONE NUMBER in international format, e.g. +37499123456")
    print("    (this is your number, NOT a password).")
    print(" 2) Telegram sends a LOGIN CODE to your Telegram app — type that code.")
    print(" 3) If you use two-step verification, it then asks for your PASSWORD.")
    print("=" * 64 + "\n")

    # Use the phone from .env if set (avoids typing it); otherwise prompt clearly.
    phone = settings.telegram_phone or input("Phone number (+countrycode...): ").strip()
    # .start() handles the code prompt, and the 2FA password prompt if enabled.
    await client.start(phone=lambda: phone)
    me = await client.get_me()
    print(f"\nLogged in as {me.first_name} (@{me.username}). "
          f"Session '{settings.telegram_session}' saved. Afon can now read your Telegram.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
