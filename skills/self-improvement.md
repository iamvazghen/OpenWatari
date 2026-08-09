# Self-improvement — how to change your own code safely

This is the master playbook, Afon. You may edit your own source to get better over time, but the
golden rule is **every change must be reversible and verified**. Never skip a step.

## The loop (follow it every time)
1. **Understand first.** `read_skill('afon-architecture')` to find the right file, then
   `read_source(path)` the file(s) you'll touch. Don't guess at structure — look.
2. **Branch.** `git_new_branch('improve/<short-name>')` so `master` stays clean and the change is
   isolated. (For a tiny, obviously-safe fix you may stay on master, but branching is the default.)
3. **Make the smallest change that works.** `write_source(path, content)` with the FULL new file.
   Match the surrounding style (see `read_skill('python')`). One concern per change.
4. **Verify.** `lint()` then `run_tests()`. **Do not proceed unless the suite is green.** If a test
   fails, read the output, fix it, and re-run. If you can't get it green, revert (step 7) and tell
   Vazghen what blocked you.
5. **Confirm.** Tell Vazghen in one or two sentences what you changed and that tests pass, and get a
   yes before committing (`write_source`, `git_commit`, `git_push` are all confirm-gated).
6. **Commit.** `git_commit("<what changed and why>")`. Commits are your save points — commit small.
7. **If anything is wrong afterwards:** `git_log()` to find the commit, then `git_revert(<hash>)`.
   A revert is a *new* commit, so nothing is lost and the bad change is undone.
8. **Push only when asked** and only after a green commit: `git_push()`.

## Hard limits (you cannot and must not cross these)
- You have **no** tool to reset, force-push, rebase, delete branches, or `git clean`. That's on
  purpose — you literally cannot rewrite or destroy history. Reversibility is guaranteed.
- You cannot read or write secrets: `.env`, `*.session`, `voiceprint.json`, `audit/`, `backups/`,
  `.git/` are all blocked. Don't try to route around that.
- Never commit code that fails tests. Never commit a secret or a key.
- Big or risky refactors: describe the plan to Vazghen and get a yes before starting, not after.

## Good first improvements
- The efficiency report (`read_source('docs/BENCHMARKS.md')`) lists concrete fine-tune targets — the
  system-prompt size is the top one. Tackle items there, test, commit.
- When you hit a rough edge mid-conversation ("that tool's wording is confusing", "this could be
  cached"), note it with `remember(...)`, and later turn it into a branch + fix.
