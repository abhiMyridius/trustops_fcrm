# Worktree protocol

Parallel subagents work in separate git worktrees so that two lanes never edit the same checkout (design C §5.3, §7.1). The lead owns `main`, and only the lead merges.

## One worktree per stream

- Each stream gets one worktree and one branch: `ws/<stream>/<topic>`. Examples: `ws/contracts/proposals`, `ws/ai/prompts-v1`, `ws/s2-scoring/routes`, `ws/web/analyst`, `ws/ops/opa`.
- Create it from the current `main`:
  ```bash
  git worktree add .claude/worktrees/<stream> -b ws/<stream>/<topic> main
  ```
  When Claude Code creates one for a subagent (`isolation: worktree`), it chooses the path. Keep the branch naming anyway.
- A subagent works only inside its worktree and only on the paths its brief lists under Outputs. It never `cd`s into another checkout and never runs git against another worktree.
- Shared state does not belong in a worktree. The git stash is shared across worktrees, so do not use a bare `git stash`. To set work aside, make a WIP commit on your own branch.
- `.cache/` is per worktree and git-ignored, so the `check.ok` marker never crosses worktrees.

## Before asking for a merge

1. Rebase on the latest `main`, not merge: `git fetch` (if remote) and then `git rebase main`. Resolve conflicts in your own lane's files. If a conflict is in another lane's file, stop and report it in Failed; do not resolve it for them.
2. Re-run the gate on the rebased tree: `make check`, then `make gate` (or the subset the brief names). A green run before the rebase does not count.
3. Make sure every commit on the branch follows `commit-convention.md` (`git log main..HEAD`).
4. Report with Changed, Failed and Decided.

## Merging (lead only)

1. Check out `main` in the primary checkout and merge the branch with `--ff-only` when possible. Otherwise use `--no-ff` with a merge commit in the convention (`chore(merge): ws/<stream>/<topic> [ADR-nn]`, `Stage:`, `Gate:`).
2. Run `make gate` on the merged tree. Merge only stays in if the gate passes on the merged result. If it fails, revert the merge commit and send the failure back to the stream.
3. Run `/code-review` on the merged diff (G-C2). A person judges rule drift: the eight rules, the visibility matrix, the version pins.
4. Remove the worktree when the stream is done: `git worktree remove .claude/worktrees/<stream>` and delete the branch.

## Ownership boundaries that cause most conflicts

| Shared file | Owner | Others |
|---|---|---|
| `src/packages/contracts/**` | contracts lane | report needed changes; never edit |
| `ai/policies/tool_permits.json` (source) → `ops/opa/data/tool_permits.json` (copy) | ai lane writes the source; ops lane owns the copy step | keep the data shape `{"tool_permits": {...}}` |
| `.claude/settings.json` (hook registration) | lead | lanes ship scripts under `ai/guidance/hooks/`, not registrations |
| `docs/governance/*.md` | anyone may append a row | nobody edits an existing row (the hook refuses) |
| `Makefile`, `docker-compose.yaml` | ops lane | other lanes add targets through the ops lane's brief |
