"""Protocol PING - send a phone push through ntfy.

Launched detached with: <parent_pid> <repo_root> <python_exe>.
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path


def _env(repo_root: Path) -> dict[str, str]:
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


def main() -> None:
    if len(sys.argv) < 3:
        return
    repo_root = Path(sys.argv[2])
    env = _env(repo_root)
    topic = env.get("AFON_NTFY_TOPIC", "")
    server = env.get("AFON_NTFY_SERVER", "https://ntfy.sh").rstrip("/")
    if not topic:
        return

    req = urllib.request.Request(
        f"{server}/{topic}",
        data=b"Afon protocol ping reached this phone.",
        headers={"Title": "Afon", "Tags": "bell"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10).close()
    except Exception:
        return


if __name__ == "__main__":
    main()
