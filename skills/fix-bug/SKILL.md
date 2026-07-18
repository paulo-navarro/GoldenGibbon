---
name: fix-bug
description: 'Work a bug from roadmap/bugs.md end-to-end: investigate, fix, test, mark as Fixed, and (when asked) open one PR per bug. Use when asked to fix bugs from the roadmap, work the bug list, or "corrija os bugs em aberto". Covers the bugs.md conventions, validation bar, and the per-bug branch/PR structure including stacked PRs.'
argument-hint: '[BUG-NNN, or blank to pick the next Open bug]'
---

# Fix a Bug from the Roadmap

## The bug list

`roadmap/bugs.md` — one `## BUG-NNN: title` section per bug with
`**Status:** Open | Fixed`, a `### Problem` (or Symptom/Root cause), and a
`### Fix direction` (hints, not gospel — verify the root cause yourself; e.g.
BUG-017's real cause was an attribute that never existed, worse than reported).

## Workflow (the user's preferred loop)

1. **One bug at a time.** Only batch bugs when truly correlated, and say so.
2. Investigate: read every file cited in the bug, confirm the root cause in
   the current code (line numbers in bugs.md drift).
3. Fix with tests: add focused tests for the new behavior AND keep existing
   tests meaningful (if a fix changes preconditions, adapt old tests to still
   exercise the mechanics — e.g. pass `force_close_grace_minutes=0` rather
   than deleting them).
4. Validate with the `run-tests` skill — the bar is **zero regressions vs
   baseline** (use its baseline-diff technique), not just "my new tests pass".
5. Update bugs.md: flip `**Status:** Open` → `Fixed`, replace
   `### Fix direction` with a `### Fix` checklist of what was actually done
   (`- [x]` items, file paths, test names). Anything requiring the user
   (e.g. `alembic upgrade head` on prod) stays as `- [ ] **Pendente (manual):**`.
6. **Stop and give feedback before moving to the next bug.** The user wants a
   summary + open questions between bugs, not a silent batch.

## Grepping for scope

Fixes here often have sibling occurrences: after fixing one instance of a bad
pattern, grep the whole repo for the pattern (e.g. the BUG-017 `getattr(...,
"_cooldown_candles", 16)` existed in 3 places, the report cited 1).

## Per-bug PRs (when the user asks)

- Branch naming: `fix/bug-NNN-short-slug`. One PR per bug, base `main`.
- **Shared files force stacking:** if two bugs touch the same file, the later
  PR's base is the earlier PR's branch (e.g. #12 based on
  `fix/bug-015-...`). Note the merge order in the PR body.
- Before splitting combined work: commit everything to a local backup branch
  (`wip/...`, not pushed). After splitting, verify integrity:
  `git diff --stat <last-stacked-branch> <backup>` must show ONLY the files of
  the branches not in that stack.
- Splitting shared files means manually reverting the later bug's hunks on the
  earlier branch — re-grep for the later bug's identifiers afterwards to prove
  the file is clean (`grep force_close_streak ...` → empty).
- Validate EACH branch in isolation (checkout, recreate schema, run its suites)
  before pushing. Push with `git push -u origin <branch>`, create PRs with
  `gh pr create` (auth is already configured, account paulo-navarro).
- bugs.md: each branch carries only its own bug's section update.

## House rules

- Conventional commits: `fix(BUG-NNN): imperative summary` + body explaining
  root cause → fix.
- Never touch the user's own stashes; `git stash list` before stashing.
- If the fix adds a DB column, remind about prod migration as a pending item.
