"""One live process per role — and a watchdog that keeps it that way.

Duplicates are a real production failure here, not a theoretical one. Two voice edges both hold the
microphone and both answer, so the owner hears Afon twice and the speaker gate fights itself; two
pc_agents race to execute the same forwarded command; two brains both tick the proactive engine and
double-send. The usual causes are mundane: a scheduled restart that overlaps a manual one, a task
relaunch while the old process is still unwinding a slow teardown, or a crash-loop that spawns
faster than it dies.

The rule, per the owner: **newest wins**. When two processes of the same role are alive, the OLDER
ones are killed. That matches how restarts are meant to work — you restart because you want the new
code — and it makes an overlapping restart self-healing instead of leaving a stale process serving
old behaviour (exactly the "edge running stale code" problem we hit before).

Two mechanisms, because either alone has a hole:

  * a **claim file** per role (``~/.afon/run/<role>.json``) recording pid, start time and host.
    Cheap, exact, and survives across restarts.
  * a **process scan** for anything running this role's module, which catches instances that never
    wrote a claim (started by hand, by a different launcher, or before this code existed).

A background thread re-checks every ``interval`` seconds, so a duplicate that appears *later* — the
overlapping-restart case — is cleaned up rather than persisting until someone notices.

Fail-open by design: if the guard cannot read a claim, scan processes, or kill anything, it logs and
lets the process run. A supervisor that refuses to start Afon because it is unsure is worse than a
duplicate.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from loguru import logger
from afon.shared.paths import state_dir

_RUN_DIR = state_dir() / "run"

# How a role is recognised in a foreign process's command line.
#
# The pattern is the full ``-m <module>`` INVOCATION, not the bare module name, and the process must
# also BE a Python interpreter. Matching the bare name is how a monitoring command, a grep, or a shell
# wrapper gets mistaken for the daemon: observed live on 2026-07-31, a bare-name scan matched three
# Git-Bash shells and a `python -c` diagnostic simply because the string appeared in their command
# lines. Killing those would have been catastrophic and completely unrelated to Afon. (This is the
# same trap restart_edge.ps1 documents — it flagged its own monitoring commands the same way.)
_ROLE_PATTERNS = {
    "edge": ("-m afon.edge.assistant",),
    "pc_agent": ("-m afon.edge.pc_agent",),
    "brain": ("-m afon.brain.server",),
}
_PY_EXE_PREFIXES = ("python", "pythonw", "py")

_started: set[str] = set()


def _claim_path(role: str) -> Path:
    return _RUN_DIR / f"{role}.json"


def _proc_start_time(pid: int) -> float | None:
    """Process start time (epoch seconds), or None if it can't be determined.

    Start time is what makes "kill the older one" safe: a PID alone can be recycled by the OS, so
    acting on PID identity risks killing an unrelated process that inherited the number."""
    try:
        import psutil

        return psutil.Process(pid).create_time()
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 — no such process / access denied
        return None
    try:  # Linux without psutil (the VPS): derive from /proc and the boot clock
        with open(f"/proc/{pid}/stat", "rb") as f:
            fields = f.read().split(b") ")[-1].split()
        ticks = float(fields[19])
        hz = os.sysconf("SC_CLK_TCK")
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
        return time.time() - uptime + ticks / hz
    except Exception:  # noqa: BLE001
        return None


def _alive(pid: int) -> bool:
    if pid <= 0 or pid == os.getpid():
        return False
    try:
        import psutil

        return psutil.pid_exists(pid)
    except ImportError:
        pass
    try:
        os.kill(pid, 0)      # signal 0 = existence check, no effect on the process
        return True
    except (OSError, ProcessLookupError):
        return False


def _kill(pid: int, why: str) -> bool:
    try:
        import psutil

        p = psutil.Process(pid)
        p.terminate()
        try:
            p.wait(timeout=5)
        except Exception:  # noqa: BLE001 — it ignored SIGTERM; insist
            p.kill()
        logger.warning(f"singleton: killed pid {pid} — {why}")
        return True
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001
        logger.warning(f"singleton: could not kill pid {pid} ({type(e).__name__}: {e})")
        return False
    try:
        import signal

        os.kill(pid, signal.SIGTERM)
        logger.warning(f"singleton: killed pid {pid} — {why}")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"singleton: could not kill pid {pid} ({type(e).__name__}: {e})")
        return False


def _related_pids() -> set[int]:
    """This process's ancestors and descendants.

    They must never count as duplicates. The edge is launched by a system-Python parent that re-execs
    into the venv interpreter, so BOTH processes' command lines contain ``afon.edge.assistant``.
    Without this exclusion the child sees its own launcher as an older duplicate and kills it —
    which took the live edge down the first time this ran. Same shape for any wrapper or supervisor."""
    me = os.getpid()
    related = {me}
    try:
        import psutil

        proc = psutil.Process(me)
        for anc in proc.parents():
            related.add(anc.pid)
        for kid in proc.children(recursive=True):
            related.add(kid.pid)
    except Exception:  # noqa: BLE001 — no psutil / access denied: fall back to self only
        try:
            related.add(os.getppid())
        except Exception:  # noqa: BLE001
            pass
    return related


def _scan_role(role: str) -> list[tuple[int, float]]:
    """Every UNRELATED live process running this role, as (pid, start_time). Needs psutil; without it
    this returns nothing and the claim file carries the guard alone."""
    pats = _ROLE_PATTERNS.get(role, ())
    if not pats:
        return []
    try:
        import psutil
    except ImportError:
        return []
    skip = _related_pids()
    out: list[tuple[int, float]] = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            if p.info["pid"] in skip:
                continue
            # Must actually BE a Python interpreter. A shell or an editor whose command line merely
            # mentions the module is not a duplicate daemon, and must never be killed as one.
            name = (p.info.get("name") or "").lower()
            if not any(name.startswith(pfx) for pfx in _PY_EXE_PREFIXES):
                continue
            cmd = " ".join(p.info.get("cmdline") or [])
            if any(pat in cmd for pat in pats):
                out.append((p.info["pid"], p.info.get("create_time") or 0.0))
        except Exception:  # noqa: BLE001 — process vanished mid-iteration / access denied
            continue
    return out


def _read_claim(role: str) -> dict | None:
    try:
        return json.loads(_claim_path(role).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_claim(role: str) -> None:
    try:
        _RUN_DIR.mkdir(parents=True, exist_ok=True)
        _claim_path(role).write_text(json.dumps({
            "role": role,
            "pid": os.getpid(),
            "started": _proc_start_time(os.getpid()) or time.time(),
            "host": os.environ.get("COMPUTERNAME") or os.uname().nodename if hasattr(os, "uname")
            else os.environ.get("COMPUTERNAME", "?"),
            "written": time.time(),
        }), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — never block startup on the claim file
        logger.warning(f"singleton: could not write the claim for '{role}': {e}")


def enforce(role: str) -> int:
    """Ensure this process is the only live one for ``role``. Returns how many duplicates were killed.

    Newest wins: any older instance is terminated. If a NEWER instance exists, this process is the
    stale one and steps aside by returning -1 — the caller decides whether to exit (the supervisor
    does), which is what stops two restarts from ping-ponging kills at each other."""
    my_start = _proc_start_time(os.getpid()) or time.time()
    killed = 0
    candidates: dict[int, float] = {}
    related = _related_pids()   # a launcher parent or a spawned child is not a duplicate

    claim = _read_claim(role)
    if (claim and isinstance(claim.get("pid"), int) and claim["pid"] not in related
            and _alive(claim["pid"])):
        candidates[claim["pid"]] = float(claim.get("started") or 0.0)
    for pid, started in _scan_role(role):
        if _alive(pid):
            candidates[pid] = started

    for pid, started in candidates.items():
        if started and started > my_start:
            logger.warning(f"singleton: a NEWER '{role}' (pid {pid}) is running — this one stands down")
            return -1
    for pid, started in candidates.items():
        if _kill(pid, f"duplicate '{role}' older than this process (pid {os.getpid()})"):
            killed += 1
    if killed:
        try:
            from afon.shared import errors as _err

            _err.record_op("guard", role, ok=False,
                           detail=f"killed {killed} duplicate/stale '{role}' process(es)")
        except Exception:  # noqa: BLE001
            pass
    _write_claim(role)
    return killed


def _watch(role: str, interval: float) -> None:
    while True:
        time.sleep(interval)
        try:
            if enforce(role) == -1:
                # A newer instance took over while we were running. Do not fight it: exit so the
                # owner is left with exactly one, and the newest code, which is why it restarted.
                logger.warning(f"singleton: newer '{role}' took over — exiting this process")
                os._exit(0)
        except Exception as e:  # noqa: BLE001 — the watchdog must never kill its own process
            logger.warning(f"singleton watch '{role}' hiccup: {type(e).__name__}: {e}")


def claim(role: str, *, interval: float = 60.0, watch: bool = True) -> bool:
    """Become the one live process for ``role``. Returns False if a newer instance already owns it.

    Call once at process start. Keeps watching in the background so a duplicate that appears later
    (the overlapping-restart case) is cleaned up rather than lingering."""
    try:
        result = enforce(role)
    except Exception as e:  # noqa: BLE001 — fail open: a guard failure must not stop Afon starting
        logger.warning(f"singleton: guard unavailable for '{role}' ({type(e).__name__}: {e})")
        return True
    if result == -1:
        return False
    if watch and role not in _started:
        _started.add(role)
        threading.Thread(target=_watch, args=(role, interval), daemon=True,
                         name=f"singleton-{role}").start()
    logger.info(f"singleton: this process owns '{role}'"
                + (f" (killed {result} duplicate(s))" if result else ""))
    return True


if __name__ == "__main__":
    # Self-check: claim/enforce are safe with no peers, a dead pid in the claim file is ignored,
    # and a NEWER peer makes us stand down instead of killing it.
    import tempfile

    _RUN_DIR = Path(tempfile.mkdtemp())
    # The scan is disabled here: a self-check must never touch production. Without this it finds the
    # REAL running edge, decides this newer process wins, and kills the owner's live assistant —
    # which is exactly what it did the first time this ran.
    _ROLE_PATTERNS = {}
    assert claim("edge", watch=False) is True, "a lone process claims its role"
    c = _read_claim("edge")
    assert c and c["pid"] == os.getpid(), "the claim records this pid"

    _claim_path("edge").write_text(json.dumps({"role": "edge", "pid": 999_999_999, "started": 1.0}),
                                   encoding="utf-8")
    assert claim("edge", watch=False) is True, "a dead pid in the claim is harmless"

    future = time.time() + 10_000
    _claim_path("brain").write_text(json.dumps({"role": "brain", "pid": os.getpid(),
                                                "started": future}), encoding="utf-8")
    # Our own pid is never treated as a peer (_alive skips self), so this must still succeed.
    assert claim("brain", watch=False) is True, "a process is never its own duplicate"
    assert _proc_start_time(os.getpid()) is not None, "start time is readable for the current process"
    assert _alive(os.getpid()) is False, "self is excluded from liveness checks"
    print("singleton self-check OK")
