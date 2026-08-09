# Acceptable Use & Responsible-AI notice

OpenAfon builds **Afon**, an autonomous, tool-using voice agent that can run shell commands,
control a browser and a PC, send messages and email, control smart-home devices, and edit and commit
code. With that power comes responsibility. This notice sets out how the project expects deployments
to behave. It complements — and does not override — the [MIT License](LICENSE), which governs the
software grant and disclaims warranty.

## You are the operator

When you deploy Afon, **you** are responsible for what it does on your behalf. The framework gives
you safety rails (see [SECURITY.md](SECURITY.md)); using them is your call. In particular:

- **Deploy only on systems and accounts you own or are authorized to use.** The PC-control, browser,
  shell, and messaging tools act with your credentials and your authority.
- **Keep the enforced confirmation tier on** for outward-facing/destructive actions, and review the
  audit log (`audit/*.jsonl`). Don't disable the guards and then run it unattended on a shared host.
- **Respect other people.** Don't use the messaging, scraping, or automation tools to spam, harass,
  impersonate, surveil people without consent, or scrape services in violation of their terms.
- **Mind third-party terms.** Cloud STT/TTS/LLM providers, Google, Notion, Telegram, GitHub, and
  media sources (e.g. YouTube via yt-dlp) each have their own Terms of Service — you accept those when
  you supply keys and use those features. Media playback is intended for personal use.
- **Protect personal data.** A configured instance holds tokens and personal memory. Keep your fork
  private, use least-privilege scopes, and don't commit `.env`, sessions, or `memory/learned`.

## What this software is not

- **Not professional advice.** Afon can be wrong, can hallucinate, and can misuse a tool. Do not
  rely on it for medical, legal, financial, or safety-critical decisions without independent review.
- **Not a guarantee.** It is provided "as is," without warranty (see the LICENSE). The proactive and
  self-improvement features act on heuristics and an LLM; verify consequential actions.

## Prohibited uses

Don't use this project to build systems whose purpose is to cause harm — including generating malware,
conducting unauthorized intrusion, mass surveillance, targeted harassment, disinformation campaigns,
or any activity illegal in your jurisdiction. Security research and authorized testing are fine; abuse
is not.

## High-risk integrations

Tools that touch the physical world or money (smart-home **locks/alarms/covers**, PC control,
outbound messaging) are confirm-gated by design. Keep them gated, and think before you arm the
optional external fleet (`AFON_FLEET_AUTHORIZED`) — it reaches shared infrastructure.

By deploying or distributing OpenAfon you agree to use it in line with this notice.
