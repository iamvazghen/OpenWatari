"""Interactive terminal setup wizard for forking Afon into your own voice assistant.

Run it once after cloning:  ``uv run afon-setup``  (or ``uv run python -m afon.setup_wizard``).

It walks you through the choices that matter — what to call your assistant, cloud vs local voice,
your LLM backend, whether this is a single machine or a 24/7 VPS brain serving multiple devices —
asks only for the keys those choices need, auto-generates the security tokens and protocol
passwords, and writes a ready ``.env`` (backing up any existing one). Everything you skip stays at
its documented default in ``.env.example`` and degrades gracefully at runtime.

The wizard never prints a secret back to the screen and never commits anything. It only writes
``.env`` (git-ignored). Re-run it any time to reconfigure.

It uses `rich` for a nicer UI when available, and falls back to plain text so it also runs on a
bare ``uv sync`` (base deps only).
"""

from __future__ import annotations

import re
import secrets
import shutil
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = REPO_ROOT / ".env.example"
OUT = REPO_ROOT / ".env"
PERSONA = REPO_ROOT / "personality" / "afon.md"
PERSONA_EXAMPLE = REPO_ROOT / "personality" / "persona.example.md"
MEMORY_DIR = REPO_ROOT / "memory"
# (example template -> user's private file) seeded on first run so the user edits private copies,
# not the shipped templates. These targets are gitignored.
PROFILE_SEEDS = [
    ("about-you.example.md", "about-you.md"),
    ("projects.example.md", "projects.md"),
    ("environment.example.md", "environment.md"),
]

# Wake phrases openWakeWord ships pre-trained (free, CPU). Anything else needs Porcupine + a
# Picovoice key — we surface that rather than silently accept an unsupported phrase.
OPENWW_PHRASES = ["hey afon", "alexa", "hey mycroft", "hey rhasspy"]

# The eight password-gated protocols (brain/protocols.py). The wizard generates a strong password
# for each so a fork is never shipped with the placeholder passwords from .env.example.
PROTOCOL_KEYS = [
    "AFON_PROTOCOL_GOODNIGHT_PASSWORD",
    "AFON_PROTOCOL_PHOENIX_PASSWORD",
    "AFON_PROTOCOL_RAGNAROK_PASSWORD",
    "AFON_PROTOCOL_BACKUP_PASSWORD",
    "AFON_PROTOCOL_PING_PASSWORD",
    "AFON_PROTOCOL_DIAGNOSTICS_PASSWORD",
    "AFON_PROTOCOL_AUDITPACK_PASSWORD",
    "AFON_PROTOCOL_CHECKPOINT_PASSWORD",
]


# --------------------------------------------------------------------------------------------------
# UI layer — rich if present, plain otherwise. Kept tiny so the wizard reads top-to-bottom.
# --------------------------------------------------------------------------------------------------
AFON_ART = r"""
 ██╗    ██╗ █████╗ ████████╗ █████╗ ██████╗ ██╗
 ██║    ██║██╔══██╗╚══██╔══╝██╔══██╗██╔══██╗██║
 ██║ █╗ ██║███████║   ██║   ███████║██████╔╝██║
 ██║███╗██║██╔══██║   ██║   ██╔══██║██╔══██╗██║
 ╚███╔███╔╝██║  ██║   ██║   ██║  ██║██║  ██║██║
  ╚══╝╚══╝ ╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝
"""

TOTAL_STEPS = 8

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt

    _c = Console()

    def splash() -> None:
        _c.print(f"[bold cyan]{AFON_ART}[/bold cyan]", highlight=False)
        _c.print("  [cyan]▂ ▄ ▆ █ ▆ ▄ ▂[/cyan]  [bold]your voice-first AI companion[/bold]  "
                 "[dim]· OpenAfon setup[/dim]\n")

    def banner(title: str, body: str) -> None:
        _c.print(Panel(body, title=f"[bold cyan]{title}[/bold cyan]",
                       border_style="cyan", expand=False, padding=(0, 2)))

    def step(n: int, title: str, body: str) -> None:
        _c.print()
        _c.print(Panel(body, title=f"[bold cyan]step {n}/{TOTAL_STEPS} · {title}[/bold cyan]",
                       title_align="left", border_style="bright_black", expand=False,
                       padding=(0, 2)))

    def say(msg: str) -> None:
        _c.print(msg)

    def ask(q: str, default: str | None = None, secret: bool = False,
            choices: list[str] | None = None) -> str:
        return Prompt.ask(q, default=default, password=secret, choices=choices)

    def yes(q: str, default: bool = False) -> bool:
        return Confirm.ask(q, default=default)

except ImportError:  # bare install — no rich yet
    def splash() -> None:
        print(AFON_ART)
        print("  your voice-first AI companion · OpenAfon setup\n")

    def banner(title: str, body: str) -> None:
        print(f"\n=== {title} ===\n{body}\n")

    def step(n: int, title: str, body: str) -> None:
        print(f"\n--- step {n}/{TOTAL_STEPS} · {title} ---\n{body}\n")

    def say(msg: str) -> None:
        print(re.sub(r"\[/?[a-z0-9 ._-]+\]", "", msg))  # strip rich markup

    def ask(q: str, default: str | None = None, secret: bool = False,
            choices: list[str] | None = None) -> str:
        suffix = f" [{default}]" if default else ""
        if choices:
            suffix = f" ({'/'.join(choices)})" + suffix
        if secret:
            import getpass
            val = getpass.getpass(f"{q}{suffix}: ").strip()
        else:
            val = input(f"{q}{suffix}: ").strip()
        return val or (default or "")

    def yes(q: str, default: bool = False) -> bool:
        d = "Y/n" if default else "y/N"
        val = input(f"{q} ({d}): ").strip().lower()
        return default if not val else val.startswith("y")


def seed_if_missing(src: Path, dst: Path) -> bool:
    """Copy a shipped template to the user's private file only if it doesn't exist yet. Never clobbers."""
    if dst.exists() or not src.exists():
        return False
    shutil.copy2(src, dst)
    return True


def mask(value: str) -> str:
    if not value:
        return "(blank)"
    if len(value) <= 6:
        return "*" * len(value)
    return f"{value[:3]}...{value[-2:]} ({len(value)} chars)"


def parse_env(text: str) -> dict[str, str]:
    """KEY=VALUE lines of an existing .env → dict, so a re-run offers current values as defaults."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


# --------------------------------------------------------------------------------------------------
# Live key validation — a wrong key today fails silently at runtime; probe it while the user is
# still at the keyboard. Every probe is a cheap authenticated GET; network-down never blocks setup.
# --------------------------------------------------------------------------------------------------
def _http_ok(url: str, headers: dict[str, str] | None = None) -> bool | None:
    """True/False = key checked; None = couldn't reach the API (offline / no httpx)."""
    try:
        import httpx

        # The one number in the repo that is deliberately NOT read from the policy table
        # (20.F3): the wizard imports nothing from brain/ or edge/ by design, because it has to
        # run before either of them is configured, and test_layering enforces that. A key check
        # the user is watching should also fail fast rather than sit for the default window.
        return httpx.get(url, headers=headers or {}, timeout=8).status_code < 400
    except Exception:  # noqa: BLE001
        return None


PROBES = {
    "deepgram": lambda k: _http_ok("https://api.deepgram.com/v1/projects",
                                   {"Authorization": f"Token {k}"}),
    "elevenlabs": lambda k: _http_ok("https://api.elevenlabs.io/v1/user", {"xi-api-key": k}),
    "notion": lambda k: _http_ok("https://api.notion.com/v1/users/me",
                                 {"Authorization": f"Bearer {k}", "Notion-Version": "2022-06-28"}),
    "telegram-bot": lambda k: _http_ok(f"https://api.telegram.org/bot{k}/getMe"),
    "groq": lambda k: _http_ok("https://api.groq.com/openai/v1/models",
                               {"Authorization": f"Bearer {k}"}),
    "cerebras": lambda k: _http_ok("https://api.cerebras.ai/v1/models",
                                   {"Authorization": f"Bearer {k}"}),
    "tavily": lambda k: _http_ok("https://api.tavily.com/usage", {"Authorization": f"Bearer {k}"}),
}


def ask_key(label: str, probe=None, keep: str = "") -> str:
    """Secret prompt: Enter keeps the existing value (masked); a typed key is live-validated."""
    hint = f" (Enter = keep {mask(keep)})" if keep else ""
    while True:
        val = ask(f"{label}{hint}", secret=True) or keep
        if not val or probe is None or val == keep:
            return val
        say("  checking the key against the live API…")
        ok = probe(val)
        if ok:
            say("  [green]key OK[/green]")
            return val
        if ok is None:
            say("  [yellow]couldn't reach the API — keeping the key; verify once online.[/yellow]")
            return val
        if not yes("  That key did NOT validate. Re-enter?", default=True):
            return val


# --------------------------------------------------------------------------------------------------
# .env rendering — copy .env.example line-by-line, swapping only the values we collected, so every
# inline comment / default the forker didn't touch is preserved verbatim.
# --------------------------------------------------------------------------------------------------
def render_env(template_text: str, overrides: dict[str, str]) -> str:
    out_lines: list[str] = []
    seen: set[str] = set()
    for line in template_text.splitlines():
        m = re.match(r"^([A-Z0-9_]+)=", line)
        if m and m.group(1) in overrides:
            key = m.group(1)
            out_lines.append(f"{key}={overrides[key]}")
            seen.add(key)
        else:
            out_lines.append(line)
    # Any override not present in the template (shouldn't happen) gets appended so nothing is lost.
    extra = [k for k in overrides if k not in seen]
    if extra:
        out_lines.append("\n# ---- added by setup wizard ----")
        out_lines += [f"{k}={overrides[k]}" for k in extra]
    return "\n".join(out_lines) + "\n"


# --------------------------------------------------------------------------------------------------
# The guided flow.
# --------------------------------------------------------------------------------------------------
def run() -> None:
    if not TEMPLATE.is_file():
        say(f"[red]Could not find {TEMPLATE}. Run this from a Afon checkout.[/red]")
        raise SystemExit(1)

    splash()
    banner("how this works", (
        "Eight short steps configure your own voice assistant and write [bold].env[/bold].\n"
        "Press Enter to accept the [dim]\\[default][/dim] shown for any question.\n"
        "Secrets are typed hidden, validated live against each provider, never printed back.\n"
        "Re-run any time — your current values become the defaults."
    ))

    cur: dict[str, str] = {}
    if OUT.exists():
        if not yes(f".env already exists at {OUT}. Reconfigure? Your current values become the "
                   "defaults — Enter keeps them (a backup is made).", default=False):
            say("Nothing changed. Bye.")
            return
        cur = parse_env(OUT.read_text(encoding="utf-8"))

    ov: dict[str, str] = {}

    # 1) Identity ---------------------------------------------------------------------------------
    step(1, "identity", "Make it YOUR assistant — these fill the persona template, no code edits.")
    name = ask("What should your assistant call itself?",
               default=cur.get("AFON_ASSISTANT_NAME") or "Afon")
    ov["AFON_ASSISTANT_NAME"] = name
    ov["AFON_USER_NAME"] = ask("Your name (blank = it won't use a name)",
                                 default=cur.get("AFON_USER_NAME", "")) or ""
    ov["AFON_USER_ADDRESS"] = ask(
        'How should it address you? ("sir", "boss", your name, or blank)',
        default=cur.get("AFON_USER_ADDRESS", "")) or ""
    ov["AFON_UNDERSTOOD_LANGUAGES"] = ask(
        "Languages it should UNDERSTAND (comma-separated)",
        default=cur.get("AFON_UNDERSTOOD_LANGUAGES") or "English")
    ov["AFON_REPLY_LANGUAGE"] = ask("Language it should always REPLY in",
                                      default=cur.get("AFON_REPLY_LANGUAGE") or "English")
    # Ensure a persona template exists (fresh clone → copy the generic example), then seed the user's
    # PRIVATE profile files from their examples so they edit private copies, not the shipped templates.
    if seed_if_missing(PERSONA_EXAMPLE, PERSONA):
        say(f"  Created [bold]personality/afon.md[/bold] from the template — edit it to shape "
            f"{name}'s voice. The identity above fills its tokens automatically.")
    else:
        say(f"  Persona at [bold]personality/afon.md[/bold] — edit the prose to shape {name}'s "
            "voice; the identity above fills its tokens automatically.")
    seeded = [dst for src, dst in PROFILE_SEEDS if seed_if_missing(MEMORY_DIR / src, MEMORY_DIR / dst)]
    if seeded:
        say("  Seeded your private profile files (fill them with your details): "
            + ", ".join(f"memory/{p}" for p in seeded))
    say(f"  Pre-trained wake phrases (free, on-device): {', '.join(OPENWW_PHRASES)}.")
    say("  Custom phrases (e.g. your assistant's own name): train a FREE openWakeWord model "
        "(no API key) and list the .onnx path here alongside phrases — see the docs site "
        "(Personalize → custom wake words). Porcupine (Picovoice key) also works.")
    wake = ask("Wake phrase(s) and/or .onnx paths, comma-separated",
               default=cur.get("AFON_WAKE_WORDS") or "hey afon")
    ov["AFON_WAKE_WORDS"] = wake

    # 2) Voice providers --------------------------------------------------------------------------
    step(2, "voice — STT & TTS", (
        "The ears and the voice.  [bold]cloud[/bold] = best quality + lowest latency "
        "(Deepgram STT + ElevenLabs TTS, two keys).\n[bold]local[/bold] = private + free "
        "(Whisper STT + Piper TTS on CPU, no keys, heavier)."
    ))
    voice_now = "local" if cur.get("AFON_STT_PROVIDER") in ("whisper", "moonshine") else "cloud"
    voice = ask("Voice stack", default=voice_now, choices=["cloud", "local"])
    if voice == "cloud":
        ov["AFON_STT_PROVIDER"] = "deepgram"
        ov["AFON_TTS_PROVIDER"] = "elevenlabs"
        ov["AFON_ELEVENLABS_API_KEY"] = ask_key(
            "ElevenLabs API key (TTS)", PROBES["elevenlabs"], cur.get("AFON_ELEVENLABS_API_KEY", ""))
        ov["AFON_ELEVENLABS_VOICE_ID"] = ask(
            "ElevenLabs voice ID", default=cur.get("AFON_ELEVENLABS_VOICE_ID", "")) or ""
        ov["AFON_DEEPGRAM_API_KEY"] = ask_key(
            "Deepgram API key (STT)", PROBES["deepgram"], cur.get("AFON_DEEPGRAM_API_KEY", ""))
        say("  Tip: for Armenian/Ukrainian set AFON_STT_PROVIDER=whisper (local) later.")
    else:
        ov["AFON_STT_PROVIDER"] = ask("Local STT", default="whisper",
                                        choices=["whisper", "moonshine"])
        ov["AFON_TTS_PROVIDER"] = ask("Local TTS", default="piper", choices=["piper", "kokoro"])
        say("  No voice keys needed. Models download on first use.")

    # 3) Brain LLM --------------------------------------------------------------------------------
    step(3, "brain — the LLM that thinks", (
        "The reasoning model + tool loop. Any OpenAI-compatible endpoint works;\n"
        "[bold]ollama[/bold] keeps it fully offline."
    ))
    backend = ask("LLM backend", default=cur.get("AFON_LLM_BACKEND") or "freellmapi",
                  choices=["freellmapi", "openai", "ollama"])
    ov["AFON_LLM_BACKEND"] = backend
    if backend == "freellmapi":
        base = ask("freellmapi base URL",
                   default=cur.get("AFON_FREELLMAPI_BASE_URL") or "http://127.0.0.1:3001/v1")
        ov["AFON_FREELLMAPI_BASE_URL"] = base
        ov["AFON_FREELLMAPI_API_KEY"] = ask_key(
            "freellmapi API key",
            lambda k: _http_ok(f"{base.rstrip('/')}/models", {"Authorization": f"Bearer {k}"}),
            cur.get("AFON_FREELLMAPI_API_KEY", ""))
    elif backend == "openai":
        base = ask("OpenAI-compatible base URL",
                   default=cur.get("AFON_FREELLMAPI_BASE_URL") or "https://api.openai.com/v1")
        ov["AFON_FREELLMAPI_BASE_URL"] = base
        ov["AFON_FREELLMAPI_API_KEY"] = ask_key(
            "OpenAI API key",
            lambda k: _http_ok(f"{base.rstrip('/')}/models", {"Authorization": f"Bearer {k}"}),
            cur.get("AFON_FREELLMAPI_API_KEY", ""))
        ov["AFON_LLM_PRIMARY_MODEL"] = ask(
            "Model id", default=cur.get("AFON_LLM_PRIMARY_MODEL") or "gpt-4o-mini")
        # The default fallback chain is freellmapi-proxy model ids — meaningless against a plain
        # OpenAI-compatible endpoint, so keep only a user-curated chain.
        ov["AFON_LLM_FALLBACK_MODELS"] = cur.get("AFON_LLM_FALLBACK_MODELS", "")
    else:
        ov["AFON_FREELLMAPI_BASE_URL"] = ask(
            "Ollama base URL",
            default=cur.get("AFON_FREELLMAPI_BASE_URL") or "http://127.0.0.1:11434/v1")
        ov["AFON_LLM_PRIMARY_MODEL"] = ask(
            "Local model", default=cur.get("AFON_LLM_PRIMARY_MODEL") or "llama3.1")
        ov["AFON_LLM_FALLBACK_MODELS"] = cur.get("AFON_LLM_FALLBACK_MODELS", "")
    say("  Optional: DIRECT fast providers for the failover chain — a `groq:` prefixed model "
        "answers in ~0.3s.")
    if yes("Add direct provider keys (Groq / Cerebras)?",
           default=bool(cur.get("AFON_GROQ_API_KEY"))):
        ov["AFON_GROQ_API_KEY"] = ask_key(
            "Groq API key (console.groq.com — free tier)", PROBES["groq"],
            cur.get("AFON_GROQ_API_KEY", ""))
        ov["AFON_CEREBRAS_API_KEY"] = ask_key(
            "Cerebras API key (cloud.cerebras.ai — free tier, blank to skip)", PROBES["cerebras"],
            cur.get("AFON_CEREBRAS_API_KEY", ""))
        say("  Use them by prefixing chain entries, e.g. "
            "[bold]AFON_LLM_PRIMARY_MODEL=groq:llama-3.3-70b-versatile[/bold].")

    # 4) Knowledge (vault) ------------------------------------------------------------------------
    step(4, "knowledge — your vault", "An Obsidian/Markdown folder is the assistant's long-term L3 memory.")
    vault = ask("Path to your notes folder (Obsidian vault)",
                default=cur.get("AFON_VAULT_PATH")
                or str(Path.home() / "Documents" / "Obsidian Vault"))
    ov["AFON_VAULT_PATH"] = vault

    # 5) Deployment shape -------------------------------------------------------------------------
    step(5, "deployment shape", (
        "[bold]single[/bold] = one machine, loopback only.\n"
        "[bold]vps[/bold] = 24/7 brain reachable by your phone / other devices (binds 0.0.0.0,\n"
        "       protected by an auth token the wizard generates)."
    ))
    shape_now = "vps" if cur.get("AFON_BRAIN_HOST") == "0.0.0.0" else "single"
    shape = ask("Deployment", default=shape_now, choices=["single", "vps"])
    if shape == "vps":
        ov["AFON_BRAIN_HOST"] = "0.0.0.0"
        # Keep an existing token across re-runs — rotating it here would silently strand every
        # already-configured device. Rotate deliberately by blanking it in .env first.
        ov["AFON_API_AUTH_TOKEN"] = cur.get("AFON_API_AUTH_TOKEN") or secrets.token_urlsafe(36)
        m = re.match(r"ws://([^:/]+)", cur.get("AFON_BRAIN_WS_URL", ""))
        host = ask("Public/Tailscale host or IP devices will connect to (for the edge URL)",
                   default=m.group(1) if m else "127.0.0.1")
        ov["AFON_BRAIN_WS_URL"] = f"ws://{host}:8765/voice"
        say("  [green]Brain API auth token set[/green] (saved to .env, shown masked at the "
            "end). Remote clients must send it as a Bearer token / ?token=.")
    else:
        ov["AFON_BRAIN_HOST"] = "127.0.0.1"

    # 6) Security: protocol passwords (generated once, kept across re-runs) -----------------------
    step(6, "security", "Strong passwords for the 8 privileged protocols (kept if already set).")
    for k in PROTOCOL_KEYS:
        ov[k] = cur.get(k) or secrets.token_urlsafe(12)
    say("  [green]Done[/green] — goodnight / phoenix / ragnarok / backup / ping / diagnostics / "
        "auditpack / checkpoint are now uniquely gated. (Saved to .env; ask the assistant to read "
        "them back is refused by design — keep your .env safe.)")

    # 7) Optional integrations --------------------------------------------------------------------
    step(7, "optional integrations", "Add now or leave blank and wire later — everything degrades gracefully.")
    if yes("Configure any optional integrations now?", default=False):
        if yes("Telegram (read + send messages, voice notes)?", default=False):
            ov["AFON_TELEGRAM_API_ID"] = ask("Telegram api_id (my.telegram.org)",
                                               default=cur.get("AFON_TELEGRAM_API_ID", "")) or ""
            ov["AFON_TELEGRAM_API_HASH"] = ask_key(
                "Telegram api_hash", None, cur.get("AFON_TELEGRAM_API_HASH", ""))
            ov["AFON_TELEGRAM_BOT_TOKEN"] = ask_key(
                "Bot token (optional, for sending)", PROBES["telegram-bot"],
                cur.get("AFON_TELEGRAM_BOT_TOKEN", ""))
            ov["AFON_TELEGRAM_PHONE"] = ask("Your phone (+countrycode) for one-time login",
                                              default=cur.get("AFON_TELEGRAM_PHONE", "")) or ""
            say("  Then run: uv run python bench/telegram_login.py (one-time interactive sign-in).")
        if yes("Web search (Tavily)?", default=False):
            ov["AFON_TAVILY_API_KEY"] = ask_key(
                "Tavily API key", PROBES["tavily"], cur.get("AFON_TAVILY_API_KEY", ""))
        if yes("Gmail + Google Calendar (one OAuth app)?", default=False):
            ov["AFON_GOOGLE_CLIENT_ID"] = ask("Google client id",
                                                default=cur.get("AFON_GOOGLE_CLIENT_ID", "")) or ""
            ov["AFON_GOOGLE_CLIENT_SECRET"] = ask_key(
                "Google client secret", None, cur.get("AFON_GOOGLE_CLIENT_SECRET", ""))
            say("  Then run: uv run python bench/google_login.py and paste the refresh token.")
        if yes("Notion (read/write/comment)?", default=False):
            ov["AFON_NOTION_TOKEN"] = ask_key(
                "Notion integration token", PROBES["notion"], cur.get("AFON_NOTION_TOKEN", ""))
        if yes("Phone push (ntfy)?", default=False):
            ov["AFON_NTFY_TOPIC"] = ask("ntfy topic (pick an unguessable string)",
                                          default=f"afon-{secrets.token_hex(4)}")

    # 8) Proactivity + fleet ----------------------------------------------------------------------
    step(8, "behaviour", "Whether the assistant may speak unprompted, and the fleet consult.")
    ov["AFON_PROACTIVE_ENABLED"] = "true" if yes(
        "Allow proactive (unprompted) reminders/nudges? Respects quiet hours + a daily budget.",
        default=(shape == "vps")) else "false"
    ov["AFON_FLEET_AUTHORIZED"] = "false"  # always off by default — opt in by editing .env

    # Write it ------------------------------------------------------------------------------------
    # Re-runs render over the EXISTING .env (not the template) so every value the user didn't
    # revisit — extra keys, hand edits, comments — survives untouched. Fresh runs use the template.
    base_text = OUT.read_text(encoding="utf-8") if OUT.exists() else TEMPLATE.read_text(encoding="utf-8")
    if OUT.exists():
        backup = OUT.with_suffix(f".bak-{datetime.now():%Y%m%d-%H%M%S}")
        shutil.copy2(OUT, backup)
        say(f"  Backed up existing .env → {backup.name}")
    OUT.write_text(render_env(base_text, ov), encoding="utf-8")

    # Summary (masked) ----------------------------------------------------------------------------
    secretish = re.compile(r"KEY|TOKEN|SECRET|HASH|PASSWORD")
    lines = []
    for k, v in ov.items():
        lines.append(f"  {k} = {mask(v) if secretish.search(k) else (v or '(blank)')}")
    banner("Wrote .env", "\n".join(lines))

    voice_extra = "cloud-voice" if voice == "cloud" else "local-voice"
    banner("install & verify", (
        "1. Install the stack you chose:\n"
        f"   [bold]uv sync --extra edge --extra {voice_extra} --extra brain "
        "--extra channels --extra identity --extra dev[/bold]\n"
        "2. Verify everything:  [bold]uv run python bench/run_all_tests.py[/bold]"
    ))
    banner(f"run {name} — the edge (ears + voice, on this machine)", (
        "Foreground (Ctrl-C to stop):\n"
        "   [bold]uv run python -m afon.edge.assistant[/bold]\n"
        f"Say [bold]“{wake.split(',')[0].strip()}”[/bold], then your command.\n"
        "Start on every boot (Windows task / launchd / systemd, auto-detected):\n"
        "   [bold]uv run python -m afon.edge.autostart install[/bold]"
    ))
    if shape == "vps":
        banner("run the brain — 24/7 on your VPS", (
            "One-time on the VPS (Ubuntu/Debian):\n"
            "   [bold]git clone <your-fork> ~/afon && cd ~/afon && "
            "bash deploy/vps/install-brain.sh[/bold]\n"
            "   then copy this .env to the VPS:  [bold]scp .env <user>@<vps>:~/afon/.env[/bold]\n"
            "Every later code deploy FROM this machine (runs the suite first, then syncs + restarts):\n"
            "   [bold]scripts/deploy_vps.sh[/bold]\n"
            "Check it:  [bold]curl http://<vps>:8766/healthz[/bold]  →  ok\n"
            "Point every device at [bold]ws://<vps-tailscale-ip>:8765/voice[/bold] with the auth "
            "token from this .env."
        ))
    else:
        banner("run the brain — locally", (
            "Same machine, second terminal:\n"
            "   [bold]uv run python -m afon.brain.server[/bold]\n"
            "Moving to a 24/7 VPS later? Re-run this wizard, pick [bold]vps[/bold], then follow "
            "the printed deploy steps."
        ))
    banner("make it yours", (
        f"Personalise [bold]personality/afon.md[/bold] to shape how {name} speaks, and fill "
        "your private profile in [bold]memory/about-you.md[/bold].\n"
        "Docs: [bold]https://openafon.vercel.app[/bold] — quickstart, devices, tools, security."
    ))


def main() -> None:
    try:
        run()
    except (KeyboardInterrupt, EOFError):
        say("\nCancelled — no changes written.")


if __name__ == "__main__":
    main()
