"""`deploy_vps.sh --prune` deletes stale code on the brain, and only ever that (TODO H2.13).

The deploy's tar adds and overwrites but never removes, so a module deleted locally stays
importable on the always-on brain: something that should fail loudly keeps working from a stale
copy while every local test passes. J4.1 made that DETECTED; this is the half that acts on it.

Acting on it is the dangerous half, so what is checked here is mostly what prune REFUSES to do.
Two things are exercised for real rather than read:

  * the drift report itself, against a stubbed remote — the tab-separated parsing, the empty-list
    case and the file count are exactly the fiddly parts that would fail first, in a deploy;
  * the ceiling expression, LIFTED OUT OF THE SCRIPT and evaluated, so a weakened threshold fails
    here instead of during the deploy that wipes a tree.

Run:
    uv run python bench/test_deploy_prune.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

passed = failed = 0


def check(ok: bool, name: str, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> int:
    deploy = (SCRIPTS / "deploy_vps.sh").read_text(encoding="utf-8")
    verify = (SCRIPTS / "verify_vps_sync.sh").read_text(encoding="utf-8")
    bash = shutil.which("bash")

    print("[1] pruning is opt-in, and unknown flags do not silently become a deploy")
    check("PRUNE=0" in deploy, "prune defaults to OFF",
          "a deploy that deletes by default is not the decision H2.13 asked for")
    check(re.search(r"--prune\)\s*PRUNE=1", deploy) is not None, "--prune turns it on")
    check(re.search(r"unknown argument", deploy) is not None,
          "an unrecognised flag aborts instead of being ignored",
          "a typo'd --prunee would otherwise deploy without pruning and look like it pruned")

    print("\n[2] it refuses in the cases where deleting would be wrong, not merely bold")
    branch = re.search(r'if \[\[ "\$PRUNE" -eq 1(.*?)\n  else', deploy, re.S)
    check(branch is not None, "the prune branch is still identifiable")
    body = branch.group(1) if branch else ""
    check('"$other_n" -eq 0' in body,
          "prune runs only when NOTHING is missing or differing",
          "missing/differing means the sync did not land — the tree is not one to delete from")
    check('"$stale_n" -gt 0' in body, "...and only when there is something stale to remove")
    check(re.search(r"grep -qE '\(\^/\|\\\.\\\.\)'", deploy) is not None,
          "a path that escapes the deploy trees blocks the prune",
          "the list comes from our own verifier, but rm on a live brain gets a second opinion")
    check("REFUSING TO PRUNE" in body, "an implausible proportion refuses loudly")
    check(re.search(r"re-verifying after prune", deploy) is not None,
          "the tree is re-verified after the delete")
    restart = deploy.index("systemctl --user restart")
    check(deploy.index("re-verifying after prune") < restart,
          "...before the brain is restarted, so a prune that did not converge never restarts")

    print("\n[3] the ceiling expression, evaluated rather than read")
    m = re.search(r"if \(\( (stale_n > .*?) \)\); then", deploy)
    check(m is not None, "the ceiling is still a single evaluable expression")
    if m and bash:
        expr = m.group(1)
        cases = [  # (stale, remote, must_refuse)
            (1, 200, False),    # an ordinary refactor deleting one file
            (5, 200, False),    # a small module removal
            (26, 4000, True),   # too many files outright, however big the tree
            (10, 20, True),     # half the tree — a broken local enumeration, not a refactor
            (60, 61, True),     # "everything is stale": the wrong REMOTE dir
        ]
        for stale, remote, must_refuse in cases:
            r = subprocess.run(
                [bash, "-c", f'stale_n={stale}; remote_n={remote}; '
                             f'if (( {expr} )); then echo REFUSE; else echo ALLOW; fi'],
                capture_output=True, text=True)
            got = (r.stdout or "").strip()
            check(got == ("REFUSE" if must_refuse else "ALLOW"),
                  f"{stale} stale of {remote} -> {'REFUSE' if must_refuse else 'ALLOW'}",
                  f"got {got!r}")

    print("\n[4] the drift report the prune reads, against a stubbed remote")
    check("DRIFT_OUT" in verify and "DRIFT_OUT" in deploy,
          "the verifier emits it and the deploy consumes it")
    if not bash or not shutil.which("sha256sum"):
        check(False, "bash + sha256sum available to run the verifier", "cannot exercise the report")
    else:
        trees = re.search(r'^TREES="([^"]+)"', verify, re.M).group(1)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # A stub `ssh` that answers as the VPS would: byte-identical to local, plus ONE file
            # that exists only there. That is exactly the shape of a rename left half-deployed,
            # and the only shape prune is allowed to act on.
            (tmp / "ssh").write_text(
                "#!/usr/bin/env bash\n"
                f"cd '{ROOT.as_posix()}' || exit 1\n"
                f"find {trees} -type f ! -path '*__pycache__*' ! -name '*.pyc' -print0 "
                "| xargs -0 sha256sum\n"
                "echo 'd41d8cd98f00b204e9800998ecf8427e  src/afon/brain/tools/mynews.py'\n",
                encoding="utf-8")
            os.chmod(tmp / "ssh", 0o755)
            drift = tmp / "drift.tsv"
            env = {**os.environ, "PATH": f"{tmp.as_posix()}{os.pathsep}{os.environ['PATH']}",
                   "AFON_VPS": "stub@example.invalid", "DRIFT_OUT": str(drift)}
            r = subprocess.run([bash, "scripts/verify_vps_sync.sh"], cwd=str(ROOT),
                               capture_output=True, text=True, env=env)
            check(r.returncode == 1, "one stale file is reported as drift (exit 1)",
                  (r.stdout or "")[-300:])
            rows = [ln.split("\t") for ln in drift.read_text(encoding="utf-8").splitlines() if ln]
            kinds = [k for k, _ in rows]
            check(kinds.count("STALE") == 1, f"exactly one STALE row ({kinds.count('STALE')})")
            check(kinds.count("MISSING") == 0 and kinds.count("DIFFERS") == 0,
                  "no MISSING or DIFFERS rows — empty lists must not emit blank entries",
                  str([r for r in rows if r[0] in ("MISSING", "DIFFERS")]))
            stale_path = next(v for k, v in rows if k == "STALE")
            check(stale_path == "src/afon/brain/tools/mynews.py",
                  "the stale path survives the hash/path splitting intact", stale_path)
            count = next((v for k, v in rows if k == "REMOTE_FILES"), "")
            check(count.isdigit() and int(count) > 1,
                  f"a remote file count is reported for the ceiling to divide by ({count})")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
