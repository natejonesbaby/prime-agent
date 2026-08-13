---
name: issue-implementer
description: Implement one stable Prime Agent issue with narrow scope and focused validation. Use when resolve-issues assigns an issue, write ownership, repository rules, and any prior review feedback.
disable-model-invocation: true
---

# Issue Implementer

Implement exactly one issue. Treat its outcome, acceptance criteria, and
non-goals as a fixed contract.

## Inputs

Expect:

- full issue text and dependencies
- assigned paths or package boundary
- shared-worktree or managed-worktree location
- prior feedback and attempt number, when applicable
- the validation evidence already available

## Workflow

1. Read `AGENTS.md`, `FORK.md`, the complete relevant files, and the affected
   tests before editing.
2. Follow the live call path with `rg`; use optional code-index tools only as a
   map and verify their claims against source.
3. Implement the smallest complete root-cause fix. Do not refactor adjacent
   code, rewrite the issue, add a framework, or fix unrelated findings.
4. Update documentation only when the change makes a public contract,
   architecture description, operator instruction, or release procedure false.
5. Run affected tests only. If a test file changes, run that exact file from
   its package directory. Never run routine local `npm test`.
6. Run `npm run check` only when the orchestrator has not reserved the single
   consolidated root check for the batch.

In a shared worktree, edit only assigned paths, do not commit, and do not run a
whole-repository formatter concurrently with another writer. In a managed
worktree, leave all changes there for the lifecycle manager to checkpoint; do
not create, remove, or force-clean worktrees yourself.

## Blockers

Stop rather than changing the contract when work is:

- too broad for one stable implementation and verification seam
- ambiguous or contradictory
- dependent on an unavailable external prerequisite
- dependent on a user or architecture decision

Name the evidence and recommend the smallest next decision. A correctable code
defect is not a blocker.

## Return Contract

Start with `Status: success` or `Status: blocked`, then report:

- summary and files changed
- focused validation commands and results
- receipt candidate SHA or diff identity supplied by the orchestrator
- documentation impact
- concerns or blocker classification

Do not append verbose command transcripts to an issue. The orchestrator owns
the concise terminal Work Log and validation receipt.
