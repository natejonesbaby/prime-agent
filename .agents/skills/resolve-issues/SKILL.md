---
name: resolve-issues
description: Resolve a scoped Prime Agent issue set in dependency order with focused validation, reusable receipts, bounded independent review, and optional managed worktrees for overlapping writers. Use when the user asks to implement or work through approved issues.
disable-model-invocation: true
---

# Resolve Issues

Resolve the selected backlog end to end without turning every correction into a
new full test and review cycle. Read `AGENTS.md`, `FORK.md`, and the other local
issue skills before starting.

## 1. Fix The Run Scope

Record:

- user objective and selected issue IDs
- dependency order and package boundaries
- explicit non-goals
- allowed external or production actions
- base commit and initial target branch SHA

Prefer GitHub issues in this fork. An explicitly supplied issue packet or local
registry is also valid. Do not broaden the run from reviewer suggestions.
Unrelated findings go into one final candidate list.

An issue is ready only when its dependencies are complete. Use at most three
parallel implementers and serialize whenever ownership is unclear.

## 2. Choose Isolation Deliberately

Default to the upstream shared-worktree model when writers have disjoint path
ownership. Tell every worker its exact scope; workers must not commit, stage
broadly, or run the root formatter concurrently. Review each issue through a
path-limited diff.

Use managed worktrees only when writers may overlap or an experiment needs
isolation. Do not create unmanaged worktrees. The lifecycle tool is:

```bash
python3 .agents/skills/resolve-issues/scripts/resolve_worktrees.py
```

If the script is missing or unavailable, serialize the overlapping work. Do not
substitute ad hoc worktrees.

### Managed lifecycle

Initialize one run and create one worktree per overlapping issue attempt:

```bash
python3 .agents/skills/resolve-issues/scripts/resolve_worktrees.py init --run <run-id> --target <branch> --base <sha>
python3 .agents/skills/resolve-issues/scripts/resolve_worktrees.py create --run <run-id> --issue <id> --attempt <n>
```

After implementation, checkpoint the isolated candidate and retain the returned
`checkpoint_sha` as its receipt identity:

```bash
python3 .agents/skills/resolve-issues/scripts/resolve_worktrees.py checkpoint --run <run-id> --issue <id> --attempt <n> --message "Resolve issue <id>"
```

After the reviewer approves that exact SHA, record approval and integrate it:

```bash
python3 .agents/skills/resolve-issues/scripts/resolve_worktrees.py approve --run <run-id> --issue <id> --attempt <n> --expected-head <sha>
python3 .agents/skills/resolve-issues/scripts/resolve_worktrees.py integrate --run <run-id> --issue <id> --attempt <n> --expected-head <sha>
```

Never approve or integrate a candidate that changed after review.

## 3. Implement And Validate

Assign each issue through `$issue-implementer`. Workers run only focused tests.
After a shared batch is consolidated, or after managed candidates are integrated,
the orchestrator runs `npm run check` once for the coherent candidate. Do not
run routine local `npm test`.

Create a validation receipt containing:

- candidate SHA or diff identity
- changed paths
- commands, exit statuses, and relevant results
- documentation-impact decision

Do not make every worker repeat the same root check. If unrelated existing work
or a pre-existing failure appears, record it and continue unless it invalidates
the selected issue.

## 4. Review Without Churn

Use `$issue-reviewer` for meaningful behavioral changes. A trivial documentation
or mechanical correction may receive a local self-review when no behavior,
contract, permission, data, or release boundary changes.

The first reviewer returns every blocking finding it can identify. For an
in-scope completion defect:

1. Send all findings to the same implementer.
2. Rerun only tests affected by the correction.
3. Return to the same reviewer for a delta review.
4. Reuse unchanged receipt evidence.

Allow at most two correction rounds for one candidate. After that, either begin
a genuine new implementation attempt and consume a retry, or classify the issue
as needing definition/reslicing. A new reviewer does not reset the count.

### Failed managed attempts

Before starting a fresh managed attempt, account for the failed attempt. If it
has dirty work worth retaining, checkpoint it first. Then record why it failed
and archive it:

```bash
python3 .agents/skills/resolve-issues/scripts/resolve_worktrees.py fail --run <run-id> --issue <id> --attempt <n> --reason "<concrete reason>"
python3 .agents/skills/resolve-issues/scripts/resolve_worktrees.py archive --run <run-id> --issue <id> --attempt <n>
python3 .agents/skills/resolve-issues/scripts/resolve_worktrees.py create --run <run-id> --issue <id> --attempt <n+1>
```

`archive` removes only a clean failed worktree and retains its branch and exact
SHA for recovery. Never integrate an archived attempt. Include every retained
branch in the final closeout so repository maintenance can delete it later only
after a deliberate decision.

Restart broader review only when a correction changes a public contract,
permissions, data-write behavior, packaging, or daemon protocol. Do not run an
automatic multi-specialist cumulative review after every issue. Use one
milestone review only when the selected objective or risk justifies it.

## 5. Integrate, Publish, And Close

Do not push each issue or correction. Stabilize the complete candidate first so
the existing full CI matrix runs once.

For a managed run, verify the integration worktree with the required focused
commands and root check, then publish only if the target still matches its
recorded SHA:

```bash
python3 .agents/skills/resolve-issues/scripts/resolve_worktrees.py publish --run <run-id> --expected-target <sha>
python3 .agents/skills/resolve-issues/scripts/resolve_worktrees.py close --run <run-id>
python3 .agents/skills/resolve-issues/scripts/resolve_worktrees.py list
```

`close` is a required terminal step: it removes only clean worktrees and merged
branches whose commits are reachable from the published target. Archived failed
branches remain in `retained_branches`; report them explicitly. If publication,
integration, archival, or cleanup fails, preserve the run and inspect it with:

```bash
python3 .agents/skills/resolve-issues/scripts/resolve_worktrees.py recover --run <run-id>
python3 .agents/skills/resolve-issues/scripts/resolve_worktrees.py cleanup --run <run-id>
```

Never force-remove, reset, clean, or delete a branch to make lifecycle status
look complete.

## 6. Durable Closeout

For each issue, record one concise terminal Work Log entry rather than a command
transcript:

- behavior changed and key files
- receipt identity and focused validation
- review outcome and material corrections
- documentation impact
- deployment/CI result or remaining blocker

Finish only when every selected issue is resolved or has a concrete non-retriable
blocker, all managed runs are closed or explicitly preserved with recovery
instructions, and one consolidated candidate is ready for CI.
