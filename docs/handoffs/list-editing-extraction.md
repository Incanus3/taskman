# List editing extraction

Status: active. Updated: 2026-11-05.
Resume: `$resume list-editing-extraction`.

## Objective and checkpoint

Extract List opening, validation and submission from Workspace into a socket-free
`ListEditing` collaborator while preserving behavior. Implementation and focused tests are
complete and independently reviewed without actionable findings. The resulting ownership
is recorded in the [workflow decomposition specification](../specs/2026-09-03-project-live-decomposition-design.md#taskmanwebprojectliveworkspace).
The operator accepted the completed workstream and requested closure on 2026-11-05;
only explicit acknowledgement of the ruling below remains before retirement.

`ListEdit` retains state and fresh target lookup. Workspace applies editor outcomes and owns
parent expansion, selected paths, topology refreshes, counts and movement refresh order.

The approved follow-up fixes blank rename validation/submission and bounds default test
concurrency to the configured pool. The concurrency contract is in the
[development guide](../guides/development.md#verification-expectations).

## Verification and remaining sequence

- Ten new direct tests were observed failing before implementation, then passing.
- The new tests and existing List editor, List LiveView, workspace and workspace-update tests
  passed together: 72 tests, using `--max-cases 16`.
- Four blank-rename regressions reproduced `String.trim(nil)` before the nil-safe normalization
  fix; the context and List LiveView suite then passed all 48 tests. Nil, empty and whitespace
  names now produce required-name errors. The editor and persisted name survive invalid
  validation/submission, and a corrected trimmed name saves successfully.
- Startup checks confirmed concurrency 16 by default, 7 with `TEST_DATABASE_POOL_SIZE=7`,
  3 with explicit `--max-cases 3`, and 1 with `--trace`.
- Ordinary `mix precommit` passed: 1,473 passed, 3 skipped, 60 browser tests excluded.
  Formatting and scoped whitespace checks passed. Browser verification was not run for this
  server-side extraction.
- Independent review compared the extracted code with the removed handlers and checked
  relevant existing state/context and LiveView coverage; no scoped findings. The review
  assessed recorded test evidence without repeating the suite. Separate independent review
  of the two follow-up fixes also found no issues.
- Implementation is locally committed as `e9d5a13f34ccd04f704cd132ecc2b520faaecb60`.
  Follow-up fixes are locally committed as `1161e749a26b34e476059d33a9b7e810a496ed7e`.
  Next: obtain acknowledgement of the ruling below; operator acceptance is complete.
  Retire this handoff only after those gates. No publication is authorized.

## Rulings awaiting acknowledgement

1. Proceed in the current session from the accepted extraction recommendation and explicit
   implementation request, without a second design/plan approval round. The recommendation
   fixed the boundary, dependencies, verification and scope. Cost if wrong: revise or undo
   the local reversible implementation if the operator wanted further design review first.
