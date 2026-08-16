"""Protocol PING - prove a phone push actually reaches the owner's phone.

Launched detached with: <parent_pid> <repo_root> <python_exe>.

The point of this protocol is to answer one question: *does the push channel work?* The previous
version could not answer it. A missing topic returned silently, and a failed POST was swallowed by
a bare ``except Exception: return`` — so the two outcomes that mean "your phone notifications are
broken" were indistinguishable from success, on the one tool whose entire job is to detect exactly
that. Asking a test whether the thing works, and having it always say yes, is worse than not
having it.

It now writes a report of what happened, which `brain/protocols.py` delivers over Telegram — a
deliberately *different* channel from the one under test, because a push-failure report delivered
by push cannot arrive.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


def _env(repo_root: Path) -> dict[str, str]:
    """Read .env directly: this runs detached, potentially from a different working directory."""
    values: dict[str, str] = {}
    env_file = repo_root / ".env"
    if not env_file.is_file():
        return values
    for raw in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _report(repo_root: Path, ok: bool, detail: str) -> None:
    backups = repo_root / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    verdict = "REACHED your phone" if ok else "DID NOT reach your phone"
    (backups / f"afon-ping-{stamp}.txt").write_text(
        f"Protocol PING — the push {verdict}.\n\n{detail}\n\n"
        f"Generated: {datetime.now().isoformat(timespec='seconds')}\n",
        encoding="utf-8")
    print(f"ping: {'ok' if ok else 'FAILED'} — {detail}")


def main() -> None:
    if len(sys.argv) < 3:
        return
    repo_root = Path(sys.argv[2])
    env = _env(repo_root)
    topic = env.get("AFON_NTFY_TOPIC", "")
    server = env.get("AFON_NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    if not topic:
        _report(repo_root, False,
                "AFON_NTFY_TOPIC is not set, so there is nowhere to send a push. "
                "Set it in .env to enable phone notifications.")
        return

    req = urllib.request.Request(
        f"{server}/{topic}",
        data=b"Afon protocol ping reached this phone.",
        headers={"Title": "Afon", "Tags": "bell"},
        method="POST",
    )
    token = env.get("AFON_NTFY_TOKEN", "")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            _report(repo_root, True, f"{server} accepted the push (HTTP {resp.status}). "
                                     f"If nothing arrived, the phone is not subscribed to the topic.")
    except urllib.error.HTTPError as e:
        _report(repo_root, False, f"{server} rejected the push: HTTP {e.code} {e.reason}. "
                                  f"The topic or token is likely wrong.")
    except Exception as e:  # noqa: BLE001 — every failure has to be reported, not swallowed
        _report(repo_root, False, f"Could not reach {server}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
