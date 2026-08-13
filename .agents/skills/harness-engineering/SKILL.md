---
name: harness-engineering
description: Preserve and evolve this Prime Agent fork's upstream-native development harness without adding duplicate gates. Use when changing validation, contributor instructions, CI, hooks, test selection, or review evidence.
disable-model-invocation: true
---

# Harness Engineering

This fork deliberately uses Prime Agent's native harness. Read `AGENTS.md`,
`FORK.md`, `package.json`, `.husky/pre-commit`, and the relevant workflow before
proposing any harness change.

## Native Validation Ladder

| Change | Required local evidence |
|---|---|
| Documentation or repo-local instructions only | Inspect the rendered diff; no code gate |
| Source code | `npm run check` once for the final candidate |
| Changed or new test | The exact test file from its package directory |
| Daemon wire change | Upstream protocol compatibility tests named in `AGENTS.md` |
| Consolidated pull request | Existing GitHub Actions matrix |
| Process stress | Existing nightly workflow |

`npm run check` formats, lints, type-checks, and runs the installer and browser
smokes. It does not run tests. Do not wrap it in a second harness or make the
full suite a local implementation, review, or commit requirement.

## Evidence Reuse

A validation receipt contains:

- candidate commit SHA, or an unambiguous diff hash before checkpointing
- changed paths
- each command, exit status, and relevant result
- documentation-impact decision

Reuse a receipt while its candidate is unchanged. A correction invalidates
only evidence whose inputs changed. A reviewer may request another command only
when the receipt is missing, stale, insufficient for an acceptance criterion,
or contradicted by the live diff.

## Guardrail Rules

- Keep upstream `AGENTS.md`, scripts, hooks, and CI unchanged unless the issue
  explicitly requires a fork patch.
- Do not add universal line-count blocking. Raise file-size or complexity as a
  review concern only when the changed design becomes harder to understand.
- Do not require architecture documentation for an internal fix that leaves
  existing documentation true.
- Require the smallest affected documentation update when a public contract,
  component boundary, operational procedure, or release behavior changes.
- Treat unrelated and pre-existing failures as recorded evidence, not as a
  reason to expand the current change.
- Keep infrastructure checks fail-clear: an unavailable optional diagnostic
  must not masquerade as a product failure.

## Changing The Harness

Change a native gate only after demonstrating a concrete failure mode and the
smallest correction. Verify the gate itself with a focused fixture or command.
Do not trigger a complete review cycle for formatting, documentation, or test
metadata corrections. Full-suite certification remains CI's responsibility.
