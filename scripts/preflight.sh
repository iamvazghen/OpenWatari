#!/usr/bin/env bash
# Assert the invariants that actually broke — the ones no unit test can see (TODO K7 / J4.4).
#
# On 2026-08-08 the Watari/Jarvis -> Afon rename produced THREE silent failures in one day, and
# the hermetic suite passed 118/118 through every one of them:
#
#   1. Enrolled face refs stayed at ~/.jarvis/faces/owner.npy while the code read ~/.afon/...
#      `_owner_refs()` returned None, so owner checks answered "not enrolled" — a correct-LOOKING
#      answer, which is why it went unnoticed for weeks.
#   2. deploy_vps.sh targeted /home/openclaw/afon + unit afon-brain; the VPS has ~/jarvis and
#      jarvis-brain. Loud (set -e + remote tar exit 2) but the message named neither cause nor fix.
#   3. The VPS .env holds 108 JARVIS_-prefixed vars while config.py declares env_prefix="AFON_".
#      This is the dangerous one: pydantic-settings has extra="ignore", so deploying the renamed
#      code would leave the brain reading ZERO configuration — no keys, no tokens, no model
#      choices — and it would start healthy and pass its health check. Silent.
#
# None of these are code defects, which is why no unit test could catch them: they are
# statements about the machines the code runs on. This script makes those statements checkable.
#
#   bash scripts/preflight.sh            # local checks only
#   bash scripts/preflight.sh --remote   # also checks the VPS (needs AFON_VPS)
#   bash scripts/preflight.sh --unpark   # ...plus the Wave-0 floor checklist (SYSTEMS.md 23.F4)
#
# --unpark answers a different question from the rest of this script. The invariants above ask
# "would a deploy work"; --unpark asks "is this deployment allowed back into production at all".
# Parking is one command, so leaving the park must not be: it refuses on any unmet Wave-0 floor,
# reads what those floors ARE from docs/SYSTEMS.md rather than from a copy that goes stale here,
# and never removes the lock itself - on a full pass it prints the command and a human still acts.
#
# Exit 0 = every invariant holds. Exit 1 = at least one does not (each is named).

set -uo pipefail
cd "$(dirname "$0")/.."

REMOTE_CHECKS=0
UNPARK_CHECKS=0
for arg in "$@"; do
  [[ "$arg" == "--remote" ]] && REMOTE_CHECKS=1
  [[ "$arg" == "--unpark" ]] && UNPARK_CHECKS=1
done

if [[ -f scripts/deploy_vps.env ]]; then
  # shellcheck disable=SC1091
  source scripts/deploy_vps.env
fi

PYBIN=$([[ -x .venv/Scripts/python.exe ]] && echo .venv/Scripts/python.exe || echo python)
# Ask PYTHON where home is, rather than trusting $HOME. The code resolves its state dirs with
# `Path.home()`, and Git Bash invoked from a non-login context (which is how the test gate calls
# this) reports a POSIX $HOME like /home/<user> that does not exist on Windows — so the check
# read a different directory from the one the brain uses and reported a missing state dir that
# was present. A preflight that disagrees with the code about where to look is worse than none.
# tr -d: python.exe prints CRLF on Windows, and command substitution strips the trailing
# newline but leaves the carriage return - which turned every derived path into
# "C:/Users/iamva<CR>/.afon" and reported a missing state dir that was plainly there.
HOME_DIR=$("$PYBIN" -c "from pathlib import Path; print(Path.home().as_posix())" 2>/dev/null | tr -d '\r' || echo "$HOME")

fails=0
pass() { echo "  ok    $1"; }
fail() { echo "  FAIL  $1"; [[ -n "${2:-}" ]] && echo "        -> $2"; fails=$((fails + 1)); }
# Loud, but not a broken invariant: a state an operator can legitimately have CHOSEN. Failing the
# gate on those trains people to ignore it, which costs more than the thing being warned about.
warn() { echo "  WARN  $1"; [[ -n "${2:-}" ]] && echo "        -> $2"; }

echo "== preflight =="

# ── 1. the env prefix the code expects, vs the .env that will feed it ──────
# sed, not `grep -oP`: Git Bash ships a grep whose -P refuses to run outside a unibyte or
# UTF-8 locale, and the failure mode was this check silently reporting "could not read" —
# which then SKIPPED the remote prefix check, the single most valuable assertion here.
PREFIX=$(sed -n 's/.*env_prefix="\([A-Z_]*\)".*/\1/p' src/afon/config.py | head -1)
# Guard the guard: an empty OR implausibly long capture means the parse itself broke. A check
# that quietly "passes" on a bad value is precisely the failure mode this script exists to end
# — the first version of this line did exactly that and reported prefix "" as ok.
if [[ -z "$PREFIX" || ${#PREFIX} -gt 20 ]]; then
  fail "could not parse env_prefix from src/afon/config.py (got: '${PREFIX}')"
else
  pass "config.py expects env prefix ${PREFIX}"
  if [[ -f .env ]]; then
    n_ok=$(grep -c "^${PREFIX}" .env || true)
    n_other=$(grep -cE '^[A-Z]+_' .env | head -1 || true)
    n_wrong=$(grep -cE '^(JARVIS|WATARI)_' .env || true)
    if [[ "$n_wrong" -gt 0 ]]; then
      fail "local .env has ${n_wrong} var(s) under an OLD prefix" \
           "the code reads ${PREFIX}* only; extra='ignore' means these are silently dropped"
    else
      pass "local .env: ${n_ok} ${PREFIX} vars, no stale-prefix vars"
    fi
  else
    echo "  skip  no local .env"
  fi
fi

# ── 1b. the test registry is complete (J6.2) ──────────────────────────────
# Lives here rather than in TESTS because it validates TESTS — registering it inside the
# list it checks would be circular. 13 files were silently unregistered when it was written.
if command -v python >/dev/null 2>&1 || [[ -x .venv/Scripts/python.exe ]]; then
  if "$PYBIN" bench/test_registry_complete.py >/tmp/afon_registry.$$ 2>&1; then
    pass "test registry complete (no silently-skipped tests)"
  else
    fail "test registry incomplete" "$(grep -E '^  FAILED' /tmp/afon_registry.$$ | head -3 | tr '
' ' ')"
  fi
  rm -f /tmp/afon_registry.$$
fi

# ── 1c. shell scripts have LF endings ─────────────────────────────────────
# A CRLF .sh runs fine when Git Bash is lenient and dies with `$'\r': command not found` the
# moment anything else invokes it — which is exactly what the new test gate does. Both this
# script and deploy_vps.sh acquired CRLF from a Windows-side edit and had to be repaired; the
# editing tool re-broke it once, silently, in the middle of fixing it.
# Detected in PYTHON, not with grep: the first version used `grep -qU $'\r'` and reported
# all four scripts as CRLF when ground truth was zero. A checker that cries wolf is the
# same defect as one that stays silent, so this asks something that cannot be ambiguous.
# The glob covers scripts/githooks/* as well: hooks carry no extension, so a "*.sh" rule misses
# them — and a CRLF hook fails SILENTLY, which is the shape of the bug that froze the code graph
# on 2026-08-09.
crlf=$("$PYBIN" -c "import pathlib,sys; ps=sorted(pathlib.Path('scripts').glob('*.sh'))+sorted(p for p in pathlib.Path('scripts/githooks').glob('*') if p.is_file()); sys.stdout.write(' '.join(q.name for q in ps if bytes([13,10]) in q.read_bytes()))" 2>/dev/null)
if [[ -n "${crlf// /}" ]]; then
  fail "shell script(s)/hook(s) have CRLF line endings: ${crlf}"        "they break under any non-Git-Bash invocation; convert with: sed -i 's/\r$//' <file>"
else
  pass "shell scripts and git hooks are LF-only"
fi

# ── 1d. how stale is the code graph, really (J0.1) ────────────────────────
# INFO, never a failure. The documented check compares the graph's built_at_commit to HEAD, which
# is always equal (the post-commit hook writes it) and so reads "fresh" through an entire session
# of uncommitted work. This prints the working-tree answer instead. It does not gate because this
# repo commits rarely by policy, so red would be the resting state — and a check that is red by
# design is one people stop reading, which is the same defect as a false green.
if [[ -f graphify-out/graph.json ]]; then
  echo "  info  $("$PYBIN" scripts/graph_fresh.py --quiet 2>/dev/null | head -1)"
fi

# ── 1e. Windows Scheduled Tasks still point at code that exists ───────────
# The rename moves the CODE. It does not move Scheduled Tasks, which keep pointing at the old
# interpreter and the old checkout — and a task whose executable is missing fails SILENTLY, so the
# edge simply never starts and nothing says why.
# Found 2026-08-09: WatariPcAgent and WatariEdgeRefresh were still registered against C:\Jarvis,
# a directory that no longer exists, while AfonEdgeGuard — the watchdog whose entire job is to
# notice a dead edge — was Disabled. No edge was running and nothing would have started one.
# Windows-only and advisory: this is a laptop-edge invariant, and the brain host has no Task
# Scheduler, so a missing powershell.exe is a skip rather than a failure.
if command -v powershell.exe >/dev/null 2>&1; then
  tasks=$(powershell.exe -NoProfile -Command '
    $bad = @()
    $off = @()
    Get-ScheduledTask -ErrorAction SilentlyContinue |
      Where-Object { $_.TaskName -match "Afon|Watari|Jarvis" } |
      ForEach-Object {
        $t = $_
        foreach ($a in $t.Actions) {
          $exe = $a.Execute
          if ($exe -and $exe -notmatch "^[a-z]+\.exe$" -and -not (Test-Path $exe)) {
            $bad += "$($t.TaskName)->missing:$exe"
          }
          # ...and the script a launcher runs. WatariEdgeRefresh executes powershell.exe (which
          # exists) with -File pointing into the deleted C:\Jarvis, so checking only Execute
          # declared it healthy. The path that matters is the one in the arguments.
          if ($a.Arguments -match "-File\s+`"?([^`"]+\.(ps1|vbs|py))`"?") {
            $f = $Matches[1]
            if (-not (Test-Path $f)) { $bad += "$($t.TaskName)->missing:$f" }
          }
        }
        # Reported separately from a broken path, because they are different events with opposite
        # responses. A missing exe is a silent breakage nobody chose. A DISABLED task is a state
        # somebody chose — deliberately, when Afon is taken out of production, which is exactly
        # what happened on 2026-08-13. Failing the code gate on an operator decision teaches
        # people to ignore the gate, and an ignored gate protects nothing.
        if ($t.State -eq "Disabled") { $off += $t.TaskName }
      }
    "BAD:" + ($bad -join " ") + "|OFF:" + ($off -join " ")' 2>/dev/null | tr -d '\r')
  bad_tasks="${tasks#BAD:}"; bad_tasks="${bad_tasks%%|OFF:*}"
  off_tasks="${tasks##*|OFF:}"
  if [[ -n "${bad_tasks// /}" ]]; then
    fail "scheduled task(s) broken: ${bad_tasks}" \
         "a task whose exe is missing fails silently — the edge never starts; re-run scripts/install_edge_*.ps1"
  else
    pass "scheduled tasks point at code that exists"
  fi
  if [[ -n "${off_tasks// /}" ]]; then
    warn "scheduled task(s) DISABLED: ${off_tasks}" \
         "the edge will not start or self-restart. Deliberate while Afon is out of production; otherwise: Enable-ScheduledTask -TaskName <name>"
  fi
fi

# ── 2. state directories are where the code looks ─────────────────────────
# The code derives these from Path.home(); a rename moves the code, never the data.
STATE_DIR="$HOME_DIR/.afon"
if [[ -d "$STATE_DIR" ]]; then
  pass "state dir $STATE_DIR exists"
else
  fail "state dir $STATE_DIR is missing" "the brain will start fresh with no history"
fi
for legacy in "$HOME_DIR/.jarvis" "$HOME_DIR/.watari"; do
  if [[ -d "$legacy" ]]; then
    # Not a failure by itself — but any file present there and ABSENT in the new dir is
    # state the running code can no longer see.
    # Only STATE counts. Enrolment scratch frames (burst_*.jpg, shot_*.jpg, diag_*.jpg) are
    # disposable by design, and listing twenty of them buries the one file that matters.
    # `run/` is pruned: singleton.py rewrites those PID/heartbeat files on every start, so they
    # are process liveness, not state. Left in, they reported a broken invariant that no action
    # could fix — and a check that cannot go green is a check people learn to ignore.
    stranded=$(cd "$legacy" && find . -maxdepth 2 -type d -name run -prune -o -type f                  \( -name '*.json' -o -name '*.jsonl' -o -name '*.npy'                     -o -name '*.sqlite' -o -name '*.db' \) -print 2>/dev/null | while read -r f; do
                 [[ -e "$STATE_DIR/${f#./}" ]] || echo "${f#./}"
               done | head -20)
    if [[ -n "$stranded" ]]; then
      fail "state stranded in $legacy (present there, absent in $STATE_DIR)" \
           "$(echo "$stranded" | tr '\n' ' ')"
    else
      pass "no stranded state in $legacy"
    fi
  fi
done

# ── 3. enrolled biometrics actually load ──────────────────────────────────
# The specific silent failure: refs exist somewhere, just not where the code reads.
if [[ -f "$STATE_DIR/faces/owner.npy" ]]; then
  pass "owner face refs present at $STATE_DIR/faces/owner.npy"
else
  if [[ -f "$HOME_DIR/.jarvis/faces/owner.npy" ]]; then
    fail "owner face refs exist ONLY at the legacy path" \
         "owner checks will answer 'not enrolled' without erroring"
  else
    echo "  skip  no owner face refs enrolled on this machine"
  fi
fi

# ── 4. the remote the deploy will actually talk to ────────────────────────
if [[ "$REMOTE_CHECKS" -eq 1 ]]; then
  if [[ -z "${AFON_VPS:-}" ]]; then
    fail "AFON_VPS is not set" "set it in scripts/deploy_vps.env to run remote checks"
  else
    REMOTE="${AFON_VPS_DIR:-/home/openclaw/afon}"
    SERVICE="${AFON_VPS_SERVICE:-afon-brain}"

    if ssh -o ConnectTimeout=10 "$AFON_VPS" "test -d '$REMOTE'" 2>/dev/null; then
      pass "remote dir $REMOTE exists"
    else
      cands=$(ssh -o ConnectTimeout=10 "$AFON_VPS" "ls -d ~/afon ~/jarvis 2>/dev/null" 2>/dev/null | tr '\n' ' ')
      fail "remote dir $REMOTE does not exist" "candidates: ${cands:-none}; set AFON_VPS_DIR"
    fi

    if ssh -o ConnectTimeout=10 "$AFON_VPS" "systemctl --user cat '$SERVICE' >/dev/null 2>&1" 2>/dev/null; then
      pass "remote unit $SERVICE exists"
    else
      cands=$(ssh -o ConnectTimeout=10 "$AFON_VPS" \
        "systemctl --user list-units --type=service --no-pager 2>/dev/null | grep -iE 'afon|jarvis' | awk '{print \$1}'" 2>/dev/null | tr '\n' ' ')
      fail "remote unit $SERVICE does not exist" "candidates: ${cands:-none}; set AFON_VPS_SERVICE"
    fi

    # REMOTE stranded state — the same check §2 runs locally, on the machine that actually
    # matters. Added 2026-08-09 after the rename deploy CAUSED this: switching the brain to the
    # afon package moved its state dir from ~/.jarvis to ~/.afon, and ten files stayed behind —
    # routines.json, macros.json, objectives.json, relationship.json, patterns.jsonl,
    # approvals.json, world_model.json and three more. The brain started, answered /healthz and
    # held a normal conversation while silently having no routines, no macros and no relationship
    # history. Nothing errored; it simply behaved like a fresh install.
    # The local half of this check existed for months. The remote half did not, which is why the
    # deploy could introduce exactly the failure the script was written to prevent.
    rstranded=$(ssh -o ConnectTimeout=10 "$AFON_VPS" \
      'test -d ~/.jarvis && cd ~/.jarvis && find . -maxdepth 2 -type d -name run -prune -o -type f \( -name "*.json" -o -name "*.jsonl" -o -name "*.npy" -o -name "*.sqlite" \) -print 2>/dev/null | while read f; do [ -e "$HOME/.afon/${f#./}" ] || echo "${f#./}"; done | head -12' \
      2>/dev/null | tr '\n' ' ')
    if [[ -n "${rstranded// /}" ]]; then
      fail "state stranded on the VPS (in ~/.jarvis, absent from ~/.afon): ${rstranded}" \
           "the brain reads ~/.afon — it will behave like a fresh install for these, without erroring"
    else
      pass "no stranded state on the VPS"
    fi

    # THE SILENT ONE. A prefix mismatch on the target is invisible at runtime: the brain
    # starts, answers /healthz, and behaves like a fresh install with no credentials.
    if [[ -n "$PREFIX" ]]; then
      # A `|| echo 0` fallback after a command that already printed a zero yields TWO lines,
      # and [[ ]] then dies with a syntax error mid-script — which surfaced as "could not read
      # .env" and skipped the single most important assertion here. Take the first line and
      # keep digits only.
      n_new=$(ssh -o ConnectTimeout=10 "$AFON_VPS" "grep -c '^${PREFIX}' '$REMOTE/.env' 2>/dev/null" 2>/dev/null | head -1 | tr -dc '0-9')
      n_old=$(ssh -o ConnectTimeout=10 "$AFON_VPS" "grep -cE '^(JARVIS|WATARI)_' '$REMOTE/.env' 2>/dev/null" 2>/dev/null | head -1 | tr -dc '0-9')
      n_new=${n_new:-0}; n_old=${n_old:-0}
      if [[ "$n_old" -gt 0 && "$n_new" -eq 0 ]]; then
        fail "remote .env has ${n_old} legacy-prefixed vars (JARVIS_/WATARI_) and 0 ${PREFIX} vars" \
             "DEPLOYING THE RENAMED CODE WOULD SILENTLY STRIP EVERY SETTING (extra='ignore')"
      elif [[ "$n_new" -gt 0 ]]; then
        pass "remote .env: ${n_new} ${PREFIX} vars"
      else
        echo "  skip  could not read $REMOTE/.env"
      fi
    fi
  fi
else
  echo "  skip  remote checks (pass --remote to include them)"
fi

# -- 5. the unpark checklist (23.F4) --------------------------------------
# Runs LAST and deliberately: it executes real gate suites, so it is the slow, decisive part. The
# checks above are invariants about the machines; this one is about whether the work is done.
if [[ "$UNPARK_CHECKS" -eq 1 ]]; then
  echo
  if "$PYBIN" scripts/unpark_check.py; then
    pass "every Wave-0 floor is met - unpark is defensible"
  else
    fail "unpark REFUSED: at least one Wave-0 floor is unmet"          "the park switch stays on; see the list above"
  fi
fi

echo
if [[ "$fails" -eq 0 ]]; then
  echo "=== preflight OK ==="
else
  echo "=== ${fails} INVARIANT(S) BROKEN ==="
fi
exit $(( fails > 0 ? 1 : 0 ))
