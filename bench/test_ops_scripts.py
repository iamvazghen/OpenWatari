"""J4.4 — the shell/ops layer, which was reachable from no test at all.

`deploy_vps.sh` is the most dangerous file in the repo: it restarts the always-on brain. Until now
nothing checked it — not that it parses, not that its safety gates still run in the right order, not
that the verifier it depends on is still wired in. Every one of those failures shows up for the
first time DURING a deploy, against the live brain, which is the worst possible moment.

This cannot exercise the deploy (that needs the VPS, and the VPS still runs the pre-rename `jarvis`
package). What it CAN do is assert the properties that made past deploys fail, all of them readable
without a network:

  * it parses at all — a syntax error in the second half is currently found half-deployed;
  * the gates still run in order, and the restart is still LAST;
  * the sync list and the verifier's list are still the same list;
  * the verifier is still called (it "existed for weeks and was called from NOWHERE");
  * the host is still not hardcoded.

    uv run python bench/test_ops_scripts.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
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


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def main() -> None:
    sh = sorted(SCRIPTS.glob("*.sh")) + sorted((ROOT / ".git" / "hooks").glob("*")) * 0
    ps = sorted(SCRIPTS.glob("*.ps1"))
    check(len(sh) >= 4, f"found the shell scripts ({len(sh)})", str([p.name for p in sh]))
    check(len(ps) >= 8, f"found the PowerShell scripts ({len(ps)})", str(len(ps)))

    print("\n[1] every ops script parses")
    bash = shutil.which("bash")
    check(bool(bash), "bash is available to syntax-check with", "cannot verify shell syntax")
    for p in sh:
        r = subprocess.run([bash, "-n", str(p)], capture_output=True, text=True)
        check(r.returncode == 0, f"{p.name} parses", (r.stderr or "").strip()[:120])

    pwsh = shutil.which("powershell") or shutil.which("pwsh")
    check(bool(pwsh), "powershell is available to syntax-check with")
    if pwsh:
        # Parse every .ps1 in ONE PowerShell start-up; launching it a dozen times costs more than
        # the rest of this file put together.
        script = (
            "$bad=@(); foreach($f in Get-ChildItem -Path '%s' -Filter *.ps1){"
            "$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
            "$f.FullName,[ref]$null,[ref]$e); if($e -and $e.Count){$bad+=$f.Name}}; "
            "if($bad){Write-Output ('BAD:'+($bad -join ','))} else {Write-Output 'OK'}"
        ) % str(SCRIPTS).replace("\\", "/")
        r = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True)
        out = (r.stdout or "").strip()
        check(out.endswith("OK"), "every .ps1 parses", out[:200] or (r.stderr or "")[:200])

    print("\n[2] the mutating scripts still fail fast")
    for name in ("deploy_vps.sh", "deploy_docs.sh"):
        body = read(SCRIPTS / name)
        check(re.search(r"^set -[a-z]*e[a-z]*o? ?", body, re.M) is not None
              and "pipefail" in body,
              f"{name} runs under `set -e` + pipefail",
              "without -e a failed step is followed by the NEXT step, on the live brain")
    # Deliberately NOT -e: it has to survive its own comparisons to report every drifting file.
    ver = read(SCRIPTS / "verify_vps_sync.sh")
    check("set -uo pipefail" in ver,
          "verify_vps_sync.sh is -u + pipefail but deliberately not -e (it reports ALL drift)")

    print("\n[3] the deploy gates still run, and still run in order")
    d = read(SCRIPTS / "deploy_vps.sh")
    stages = [
        ("preflight", r"preflight\.sh"),
        ("target exists", r"test -d"),
        ("unit exists", r"systemctl --user cat"),
        ("full test suite", r"run_all_tests\.py"),
        ("sync", r"^tar .*\| ssh"),
        ("verify sync", r"verify_vps_sync\.sh"),
        ("restart", r"systemctl --user restart"),
    ]
    pos = {}
    for label, pat in stages:
        m = re.search(pat, d, re.M)
        check(m is not None, f"the deploy still has its '{label}' stage",
              "a safety gate has been removed from the deploy")
        if m:
            pos[label] = m.start()
    order = [label for label, _ in stages if label in pos]
    check(all(pos[a] < pos[b] for a, b in zip(order, order[1:])),
          "...and they appear in that order",
          str(sorted(pos, key=pos.get)))
    if "restart" in pos:
        check(pos["restart"] == max(pos.values()),
              "the RESTART is last — never before the suite or the drift check",
              "restarting first means a failed verification restarts anyway")

    print("\n[4] the sync list and the verifier's list are the same list")
    # They are written out separately in two files. If they drift, the deploy pushes a tree the
    # verifier never looks at — which is exactly how `personality/` stayed months stale.
    tar_m = re.search(r"^tar [^|]*?-czf - ([^|]+)\|", d, re.M)
    trees_m = re.search(r'^TREES="([^"]+)"', read(SCRIPTS / "verify_vps_sync.sh"), re.M)
    check(bool(tar_m) and bool(trees_m), "both lists are still machine-readable",
          f"tar={bool(tar_m)} trees={bool(trees_m)}")
    if tar_m and trees_m:
        tarred = sorted(w for w in tar_m.group(1).split() if not w.startswith("-"))
        verified = sorted(trees_m.group(1).split())
        check(tarred == verified, "the deploy syncs exactly what the verifier checks",
              f"tar={tarred} verify={verified}")
        for t in tarred:
            check((ROOT / t).is_dir(), f"...and '{t}' exists to be synced")

    print("\n[5] the verifier and preflight are actually WIRED IN")
    # `verify_vps_sync.sh` "existed for weeks and was called from NOWHERE". A verifier nobody runs
    # is worse than none, because its presence implies the check is happening.
    check("verify_vps_sync.sh" in d, "deploy calls the drift verifier")
    check("preflight.sh" in d, "deploy calls preflight")
    for script in ("verify_vps_sync.sh", "preflight.sh"):
        check((SCRIPTS / script).is_file(), f"...and {script} exists")

    print("\n[6] the deployment topology is still not hardcoded")
    host_pat = re.compile(r"\b[a-z_][\w.-]*@(?:\d{1,3}(?:\.\d{1,3}){3}|[a-z0-9-]+\.[a-z]{2,})",
                          re.I)
    ip_pat = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    for p in sh + ps:
        body = read(p)
        hits = [h for h in host_pat.findall(body) if "example" not in h.lower()]
        ips = [i for i in ip_pat.findall(body)
               if not i.startswith(("127.", "0.0.0.0", "255.")) and i != "1.2.3.4"]
        check(not hits and not ips, f"{p.name} hardcodes no host",
              f"{hits or ''}{ips or ''} — that leaks the topology into the repo")
    env = SCRIPTS / "deploy_vps.env"
    if env.exists():
        ignored = "scripts/deploy_vps.env" in read(ROOT / ".gitignore")
        check(ignored, "the real deploy_vps.env is gitignored",
              "the host and account would be committed")
    check((SCRIPTS / "deploy_vps.env.example").is_file(),
          "an .example is checked in so the variable is discoverable")

    print("\n[7] every script referenced by another script exists")
    # A rename that misses one call site fails only when that branch runs — which for ops scripts
    # is during an incident.
    referenced: dict[str, set] = {}
    for p in sh + ps:
        for m in re.finditer(r"scripts[/\\]([\w.-]+\.(?:sh|ps1|py))", read(p)):
            referenced.setdefault(m.group(1), set()).add(p.name)
    check(bool(referenced), f"found {len(referenced)} cross-references", str(sorted(referenced)))
    for target, callers in sorted(referenced.items()):
        check((SCRIPTS / target).is_file(), f"{target} exists (called by {sorted(callers)})")

    print(f"\n=== {passed}/{passed + failed} checks passed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
