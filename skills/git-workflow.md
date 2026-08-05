# Git workflow — your save-points

You commit so that nothing you do is permanent until it's proven, and everything is reversible.

## What you can do (and the tool for it)
- See state: `git_status`, `git_diff`, `git_log`.
- Isolate work: `git_new_branch('improve/<name>')`.
- Save: `git_commit('<message>')` — stages all changes (or named `paths`) and commits as Jarvis.
- Share: `git_push` — pushes the current branch to GitHub `origin` (confirm first; needs origin set).
- Undo: `git_revert('<hash>')` — a NEW commit that reverses an old one. Nothing is lost.

## What you deliberately cannot do
No reset, force-push, rebase, amend, `git clean`, or branch deletion exist as tools. You cannot lose
work or rewrite history. If you think you need one of those, you don't — use `git_revert` instead,
or ask Vazghen.

## Commit messages
One line summary, imperative-ish, then a blank line and a short why if it isn't obvious. Example:
`Cache vault search results (repeat lookups were ~40ms each)`. Your commits are automatically
trailed with "Made by Jarvis (self-improvement)" so they're easy to find and audit.

## Discipline
- Commit **small and green**. A commit should leave the suite passing.
- One logical change per commit. Don't mix a refactor with a feature.
- Branch for anything non-trivial; merge to master only after tests pass (ask Vazghen to merge, or
  fast-forward via a commit on master once confirmed).
- Before `git_push`, you must have a green `run_tests()` and Vazghen's yes.

## The remote
GitHub `origin` is configured (`iamvazghen/OpenWatari`, PRIVATE). Pushing is therefore possible and
outward-facing, so it stays gated on a green `run_tests()` and Vazghen's explicit yes — never push
because a change looks finished. Committing locally is always safe and fully reversible.
