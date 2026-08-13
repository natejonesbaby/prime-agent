---
name: generate-issues
description: Create a small, dependency-aware set of Prime Agent development issues from an approved objective or concrete repository evidence. Use when work needs to be sliced into stable implementation and validation units before resolve-issues runs.
disable-model-invocation: true
---

# Generate Issues

Generate only the issues needed to achieve the stated objective. In this fork,
prefer GitHub issues and the upstream `pkg:*` labels; use a local issue registry
only when the user explicitly chooses one.

## Establish The Contract

Read `AGENTS.md`, `FORK.md`, the relevant source and tests, and existing open
issues. Record:

- desired outcome and current evidence
- package and likely write scope
- explicit non-goals
- dependencies and external decisions
- whether the work changes upstream core or only fork-maintenance files

Do not create an issue from a hunch. Link each issue to code, a failing example,
an approved design, or another concrete source.

## Slice For Reliable Completion

An issue is one retry unit. Keep one coherent behavioral outcome and one
independent verification seam together, even when it crosses several layers.
Split when work crosses independently failing state, protocol, recovery, or
deployment boundaries. Do not split merely because several files are involved.

Prefer a direct fix over an issue for a trivial edit. Avoid umbrella issues,
general cleanup, speculative hardening, new frameworks, and review-only chores.
If the outcome still requires a product or architecture choice, stop and ask
for that choice instead of encoding alternatives in acceptance criteria.

## Required Issue Content

Each issue must contain:

1. **Outcome** — what becomes true for a user or caller.
2. **Evidence** — current behavior and concrete source anchors.
3. **Scope** — included behavior, likely package, and explicit non-goals.
4. **Acceptance criteria** — observable, unambiguous requirements.
5. **Validation** — exact focused test files or commands, plus `npm run check`
   when source changes. Never prescribe routine local `npm test`.
6. **Documentation impact** — named docs to update, or a reason none become
   false.
7. **Dependencies** — issue links and sequencing only when necessary.
8. **Release impact** — ordinary CI, daemon protocol, packaging, or deployment
   evidence required.

For GitHub issues, apply every affected label from `pkg:agent`, `pkg:ai`,
`pkg:coding-agent`, and `pkg:tui`.

## Quality Pass

Review the proposed set once as a whole before creation:

- remove duplicates and symptoms covered by the same root fix
- confirm each issue can be implemented and reviewed without redefining it
- confirm dependencies do not serialize unrelated work
- name exact focused validation rather than the entire suite
- ensure ordinary corrections will not require a new issue

Use an additional specialist only when the issue changes authentication,
external writes, data integrity, release packaging, or the daemon wire
protocol. Do not launch a general multi-review cycle for a small issue set.

Present a compact issue table before creating external issues unless the user
already approved creation. Report what was created, skipped as duplicate, or
left pending a decision.
