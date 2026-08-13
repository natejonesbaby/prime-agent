---
name: issue-reviewer
description: Review one Prime Agent issue candidate against its stable contract using existing validation evidence. Use when resolve-issues needs one complete approval or rejection and, if corrected, a bounded delta re-review.
disable-model-invocation: true
---

# Issue Reviewer

Review one candidate without editing it. Inspect the issue, the exact candidate
diff or checkpoint, repository rules, and the supplied validation receipt.

## Scope

A blocking finding must map to one of:

- an unsatisfied acceptance criterion
- a regression introduced by the candidate
- a violated repository rule or required gate
- an in-scope safety, data, packaging, or daemon-protocol defect

Do not reject for a generalized framework, adjacent cleanup, speculative
hardening, a preferred design outside the contract, or an unrelated existing
problem. Put evidence-backed adjacent observations under `Candidate findings`.

## Review Once, Completely

1. Read the full issue and all changed files.
2. Walk every acceptance criterion and named non-goal.
3. Inspect affected callers, tests, and public contracts when the diff requires
   it.
4. Return all currently knowable blocking findings in one verdict. Do not hold
   small findings for later rounds.
5. Check documentation impact: name a specific statement or operator procedure
   made false by the change before requiring a doc edit.

## Reuse Validation

Accept a receipt when its candidate SHA or diff identity matches the reviewed
candidate and its commands cover the issue. Do not rerun a test merely because
responsibility changed hands. Request or run another focused command only when
evidence is missing, stale, contradicted, or inadequate for a named criterion.
Never request routine local `npm test`; full-suite certification belongs to the
existing CI matrix.

For a correction, review only the delta plus any criterion affected by that
delta. Reuse all unaffected evidence. A documentation, formatting, or test-only
correction does not restart semantic review.

## Verdict

Start with exactly `Verdict: APPROVED` or `Verdict: REJECTED`.

For approval, map each criterion to code or validation evidence and state the
reviewed candidate identity.

For rejection, include:

- `Rejection class: completion_defect` for a bounded correction, or
  `Rejection class: contract_problem` when the issue needs definition/reslicing
- every blocking finding with file evidence
- the smallest direct correction for each finding
- which receipt evidence remains reusable

Keep candidate findings separate; they do not change the verdict.
