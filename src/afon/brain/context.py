"""Build the assistant's system prompt from its personality + markdown memory files.

The persona file (``personality/<AFON_PERSONA_FILE>``, default afon.md) is a TEMPLATE for who it
is — identity tokens are filled from config (see ``_apply_identity``). ``memory/*.md`` is the user's
profile (who they are, projects, environment) + the proactive mandate; personal profile files are
gitignored and fall back to shipped ``*.example.md`` templates. All plain Markdown, editable without
touching code.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from afon.shared.language import MATCH as LANG_MATCH
from afon.config import settings

# repo root = .../src/afon/brain/context.py -> parents[3]
_REPO_ROOT = Path(__file__).resolve().parents[3]
PERSONALITY_DIR = _REPO_ROOT / "personality"
MEMORY_DIR = _REPO_ROOT / "memory"

# Operating rules live in their own boilerplate so they're editable without touching Python.
# Override the path with AFON_OPERATING_RULES_FILE in .env if you've put a custom one elsewhere.
_OPERATING_RULES_PATH = (
    PERSONALITY_DIR / getattr(settings, "operating_rules_file", None)
    if getattr(settings, "operating_rules_file", None)
    else PERSONALITY_DIR / "operating-rules.md"
)
# Fallback shipped inside the code so a missing/corrupt boilerplate still boots with sane defaults.
#: 25.F3 — what goes in the prompt when the owner's rules file cannot be read. It is deliberately
#: NOT a second copy of those rules. The copy that lived here had already drifted from the file
#: (the file names the destructive verbs, this did not), so a brain that fell back was running a
#: rulebook the owner had never seen and could not edit — the IDENTITY.md/SOUL.md failure exactly.
#: Better to be one rule short and say so than to be silently governed by the wrong text.
_RULES_UNREADABLE = (
    "# Operating rules unavailable\n"
    "Your operating-rules file could not be read, so you are running without the owner's own "
    "rules. Say so if he asks why you are behaving differently, and stay conservative: confirm "
    "anything outward-facing or hard to undo."
)


def _persona_path() -> Path:
    """The persona template file (configurable via AFON_PERSONA_FILE)."""
    return PERSONALITY_DIR / (settings.persona_file or "afon.md")


def _identity_tokens() -> dict[str, str]:
    """Fill-ins that turn the generic persona TEMPLATE into THIS user's assistant (framework layer).

    The persona file uses ``{assistant_name}``, ``{owner_possessive}``, ``{address_line}`` and
    ``{language_line}``; everything personal comes from config (.env / the setup wizard), so the
    same shipped persona becomes anyone's assistant without editing prompts or code.
    """
    s = settings
    name = (s.user_name or "").strip()
    addr = (s.user_address or "").strip()
    ref = name or addr or "you"
    owner_possessive = f"{name}'s" if name else "your"
    if addr and name:
        address_line = f'Address {name} as "{addr}".'
    elif addr:
        address_line = f'Address them as "{addr}".'
    elif name:
        address_line = f"Address them as {name}."
    else:
        address_line = "Address them naturally, without honorifics."
    understood = (s.understood_languages or "English").strip()
    reply = (s.reply_language or "English").strip()
    if reply.strip().lower() == LANG_MATCH:
        # Mirror mode. The per-turn note in `agent._language_note` names the actual language, so
        # this only has to establish the rule; the model is never left to infer it from a two-word
        # utterance, which is where language-matching normally goes wrong.
        # Kept deliberately short. This line is in the prompt on EVERY turn and the budget is 2000
        # tokens; the first draft cost 27 of them over. The specifics belong in the per-turn note
        # (`agent._language_note`), which only appears when there is a language to name.
        # The list of languages is deliberately NOT enumerated here. In mirror mode the per-turn
        # note names the actual language, so the list is redundant — and enumerating it would make
        # the always-on prompt grow every time the owner adds a language, which is how a budget
        # gets breached by a config change nobody connects to it.
        language_line = ("They are multilingual. **Reply in the same language they used** — each "
                         "turn names it. Names and quotations stay unchanged.")
    elif understood.lower() != reply.lower():
        language_line = (f"They may speak {understood} — understand any of them, but **always reply "
                         f"in {reply}**. Never switch languages even if they do.")
    else:
        language_line = f"Speak and understand {reply}."
    return {
        "{assistant_name}": s.assistant_name or "Afon",
        "{owner_possessive}": owner_possessive,
        "{address_line}": address_line,
        "{language_line}": language_line,
        "{user_ref}": ref,
    }


def _apply_identity(text: str) -> str:
    for token, value in _identity_tokens().items():
        text = text.replace(token, value)
    return text

# Always-on context: injected into EVERY turn, so it is kept deliberately lean (see
# fine-tuning.md, Item 1). Order matters: who the owner is, the proactive mandate, his ventures,
# then the environment. Two files are intentionally NOT here:
#   * tools.md       — duplicated the tool schemas the model already receives every turn.
#   * openclaw-fleet.md — its actionable rule (delegate to ispir only) is already in the persona.
# Both stay on disk as on-demand reference (readable via read_source / the vault), they're just
# not paid for on every turn. Files are loaded ONLY if listed here (no glob) to keep the prompt
# disciplined — a new memory file must be added explicitly and weighed against the token budget.
_ALWAYS_ON = [
    "about-you.md",
    "proactive-companion.md",
    "projects.md",
]
# Demoted to on-demand reference (kept on disk, not injected every turn): environment.md (ports/
# paths the brain reads from config, not the prompt), plus tools.md and openclaw-fleet.md.


def _read(p: Path) -> str:
    try:
        text = p.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.warning(f"brain context: missing {p.name}")
        return ""
    # Strip HTML comments — they're human-facing documentation, not for the LLM. Comments are kept
    # in the FILE so authors see the placeholder docs, but excluded from the prompt. This used to
    # strip only the LEADING one, and operating-rules.md (which carries a second, mid-file note and
    # was not read through here at all) shipped ~470 characters of editor instructions to the model
    # on every single turn.
    import re
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return text.strip()


def _resolve_memory(name: str) -> Path | None:
    """Prefer the user's private profile file; fall back to the shipped ``.example`` template.

    Personal profile files (about-you.md, projects.md, …) are gitignored — a fresh clone only has
    the ``*.example.md`` templates, so the assistant still boots with generic context until the user
    fills in (or the setup wizard copies) their own.
    """
    real = MEMORY_DIR / name
    if real.exists():
        return real
    example = MEMORY_DIR / name.replace(".md", ".example.md")
    return example if example.exists() else None


def load_memory_files() -> list[tuple[str, str]]:
    """Return (filename, content) for each ALWAYS-ON memory file, in order, missing ones skipped.

    Only files in ``_ALWAYS_ON`` are loaded — other ``memory/*.md`` are on-demand reference and
    deliberately excluded from the per-turn prompt (see ``_ALWAYS_ON`` note above). Each resolves to
    the user's private file if present, else the shipped ``.example`` template.
    """
    out: list[tuple[str, str]] = []
    for name in _ALWAYS_ON:
        p = _resolve_memory(name)
        if p is not None:
            out.append((name, _read(p)))
    return out


def _learned_digest() -> str:
    """Recent learned facts (L1) as a short bullet list for the system prompt."""
    if not settings.memory_enabled:
        return ""
    try:
        from afon.brain.memory import STORE

        facts = STORE.recent_digest(settings.memory_digest_max)
        # Bound each line so the injected digest can't drift the system prompt over its token budget
        # as facts accumulate (the full fact is always reachable via `recall`; this is just a teaser).
        cap = settings.memory_digest_fact_chars
        lines = []
        for f in facts:
            f = f.strip()
            if len(f) > cap:
                f = f[: cap - 1].rstrip() + "…"
            lines.append(f"- {f}")
        return "\n".join(lines)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"learned-memory digest unavailable: {e}")
        return ""


def _vault_reachable(p: Path) -> bool:
    """True only if the path is a directory we can actually list (not a cloud placeholder)."""
    try:
        if not p.is_dir():
            return False
        next(p.iterdir(), None)  # touch it — proves read access, not just existence
        return True
    except OSError:
        return False


def validate_vault(retries: int = 3, grace: float = 0.4) -> tuple[bool, str]:
    """L3 must always be readable. Returns (ok, message) and logs loudly if not.

    A momentary blip — the 15-min vault sync's delete→move window, an antivirus lock, or a
    OneDrive 'online-only' placeholder rehydrating — must NOT fire the 'I've lost your vault'
    alarm. So we retry with a short grace; only a *sustained* failure is reported as lost access.
    """
    import time

    if not settings.vault_path:
        msg = "Obsidian vault (L3) is NOT configured — set AFON_VAULT_PATH to the local mirror."
        logger.warning(msg)
        return False, msg
    p = Path(settings.vault_path)
    for attempt in range(retries):
        if _vault_reachable(p):
            n = sum(1 for _ in p.rglob("*.md"))
            logger.info(f"Obsidian vault (L3) ready: {n} notes at {p}")
            return True, f"vault ready ({n} notes)"
        if attempt < retries - 1:
            time.sleep(grace)  # transient? give the sync/placeholder a moment to settle
    msg = f"Obsidian vault path '{settings.vault_path}' is not a readable folder."
    logger.warning(msg)
    return False, msg


def stable_prefix() -> str:
    """02.R2 — the leading bytes of the system prompt that do NOT change between turns.

    Exists so the gate can assert the property instead of re-deriving the section order, and so a
    future section has one obvious question to answer: does it belong before this line or after it.
    """
    full = build_system_prompt()
    for marker in _VOLATILE_MARKERS:
        i = full.find(marker)
        if i != -1:
            return full[:i]
    return full


#: The first line of each volatile section. A section with no marker here would be invisible to
#: `stable_prefix`, so `test_prompt_prefix_stable.py` fails if a volatile section appears that this
#: tuple does not name.
_VOLATILE_MARKERS = ("# Recently learned about", "Auto-delegate:")


def build_system_prompt() -> str:
    """Assemble the full system prompt: persona + memory, with clear section headers.

    Section ORDER is load-bearing (02.R2): everything stable first, everything that can differ
    between two turns of one session last. See `volatile` below.
    """
    persona = _apply_identity(_read(_persona_path()))
    parts: list[str] = []
    # 02.R2 — anything that can differ between two turns of the same session goes in here and is
    # appended LAST, so the bytes ahead of it are identical every time and a provider's prompt
    # cache can actually hit them. The learned digest rebuilds every
    # `memory_digest_refresh_every_turns` turns and the delegation hint moves as domains repeat;
    # both used to sit in the MIDDLE, which invalidated the cache for everything after them —
    # including the persona's second half, the Composio catalogue and the whole operating-rules
    # block. Order within the prompt is not free to choose, but this half of it was never chosen.
    volatile: list[str] = []
    if persona:
        parts.append(persona)
    mem_blocks = [f"## {name}\n{content}" for name, content in load_memory_files() if content]
    if mem_blocks:
        parts.append(
            "# Context you carry (draw on it naturally; don't recite it)\n\n"
            + "\n\n".join(mem_blocks)
        )
    digest = _learned_digest()
    if digest:
        who = (settings.user_name or "the user").strip()
        volatile.append(
            f"# Recently learned about {who} (use `recall` for older)\n" + digest
        )
    # Learned delegation bias (one line, only once domains repeat) — see fleet.routing_hint.
    try:
        from afon.brain.fleet import routing_hint

        hint = routing_hint()
        if hint:
            volatile.append(hint)
    except Exception:  # noqa: BLE001
        pass
    # T12 — Composio catalog awareness. Without this, the LLM has no idea that 17 apps with
    # hundreds of actions exist and defaults to "I can't" when the right tool is one
    # composio_find_tools() away. Inject a compact summary so every tool is on the LLM's radar.
    try:
        from afon.brain.composio_catalog import get_summary
        catalog_summary = get_summary()
        if catalog_summary:
            parts.append(catalog_summary)
    except Exception:  # noqa: BLE001
        pass

    # 01.R4 — what he genuinely CANNOT do, generated from the live config rather than written.
    # Only the dark integrations are named: listing the working ones would restate the catalogue
    # the model already holds, and the useful fact is always the negative one. Without this,
    # "text her" against an unconfigured Twilio produced a tool call, a not-configured string and
    # an apology, where one honest sentence up front was the whole answer.
    try:
        from afon.brain.selfmodel import prompt_block

        gaps = prompt_block()
        if gaps:
            parts.append(gaps)
    except Exception:  # noqa: BLE001
        pass

    # Operating rules — loaded from `personality/operating-rules.md` so anyone can fork the repo
    # and customise the rules without touching Python. See the top of that file for the pattern.
    # Falls back to a minimal hardcoded default only if the file is missing/corrupt (so a brand-new
    # brain without the boilerplate still ships with sane behaviour).
    try:
        from afon.config import settings as _s  # noqa: F401 — imported for type only
        op_rules_path = _OPERATING_RULES_PATH
        if op_rules_path.is_file():
            rules_text = _read(op_rules_path)
            if rules_text:
                parts.append(rules_text)
        else:
            logger.warning(f"operating rules missing at {_OPERATING_RULES_PATH} — running without "
                           "the owner's rules, and saying so in the prompt")
            parts.append(_RULES_UNREADABLE)
    except Exception as e:  # noqa: BLE001 — a brain with no operating rules is worse than defaults
        # Falling back is right; falling back SILENTLY is not. These rules are how the owner
        # customises behaviour, so an unreadable file means Afon quietly ignores every rule the
        # owner wrote and behaves like a fresh install — a behaviour change with no symptom to
        # notice. Log it loudly enough that "he stopped following my rules" is diagnosable.
        logger.warning(f"operating rules unreadable at {_OPERATING_RULES_PATH} "
                       f"({type(e).__name__}: {e}) — running without the owner's rules")
        parts.append(_RULES_UNREADABLE)
    return "\n\n".join([*parts, *volatile])


if __name__ == "__main__":
    sp = build_system_prompt()
    print(sp)
    print(f"\n--- system prompt: {len(sp)} chars, ~{len(sp)//4} tokens ---")
