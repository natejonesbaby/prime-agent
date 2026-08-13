# Fork Maintenance

This repository tracks [Prime Agent](https://github.com/PrimeIntellect-ai/prime-agent)
with a deliberately small local diff. Prime Agent remains the runtime and the
upstream repository remains the source for core behavior.

## Boundaries

- Keep upstream source, `AGENTS.md`, package scripts, hooks, and CI intact.
- Keep fork-maintenance instructions under `.agents/skills/` and this file.
- Put business behavior, Gmail integration, deployment configuration, and
  organization-specific skills in the consuming project, not this fork.
- Patch Prime Agent core only when an upstream extension, skill, package, or
  configuration surface cannot provide the required behavior. Record why the
  patch is necessary and its focused verification.
- Do not add a second issue registry, test runner, architecture ledger, or
  universal file-length gate to this fork.

## Validation

Follow the upstream rules in `AGENTS.md`:

- Documentation-only and repo-local instruction changes do not require the
  code gate.
- After source changes, run `npm run check` once for the final candidate.
- Run only affected tests from their package directory. If a test file changes,
  run that exact file.
- Do not run the full suite locally as a routine implementation or review step.
  The existing GitHub Actions matrix is the full-suite release signal.
- Reuse validation evidence while the reviewed commit or diff is unchanged.

## Upstream Sync

Configure the upstream remote once:

```bash
git remote add upstream https://github.com/PrimeIntellect-ai/prime-agent.git
```

Refresh the fork with a normal merge so published history is not rewritten:

```bash
git fetch upstream --tags
git checkout main
git merge upstream/main
npm ci
npm run check
git push origin main
```

Before deployment, record both the deployed commit and its upstream merge base:

```bash
git rev-parse HEAD
git merge-base HEAD upstream/main
```

## Parallel Work

Agents may share one worktree when their write scopes are disjoint. They must
name owned paths, avoid broad staging, and leave the final root check to the
orchestrator after consolidation.

Use managed worktrees only when writers may overlap or an experiment needs
isolation. The repo-local `resolve-issues` skill owns the complete lifecycle:
creation, exact-checkpoint review, integration, publication, and safe cleanup.
An unresolved conflict or dirty worktree is preserved for recovery, never
force-removed.
