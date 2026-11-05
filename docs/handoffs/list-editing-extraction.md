# List editing extraction

Status: active. Updated: 2026-11-05.
Resume: `$resume list-editing-extraction`.

## Objective and checkpoint

Extract List opening, validation and submission from Workspace into a socket-free
`ListEditing` collaborator while preserving behavior. Implementation and focused tests are
complete and independently reviewed without actionable findings. The resulting ownership
is recorded in the [workflow decomposition specification](../specs/2026-09-03-project-live-decomposition-design.md#taskmanwebprojectliveworkspace).

`ListEdit` retains state and fresh target lookup. Workspace applies editor outcomes and owns
parent expansion, selected paths, topology refreshes, counts and movement refresh order.

## Verification and remaining sequence

- Ten new direct tests were observed failing before implementation, then passing.
- The new tests and existing List editor, List LiveView, workspace and workspace-update tests
  passed together: 72 tests, using `--max-cases 16`.
- `mix precommit --max-cases 16` passed: 1,469 passed, 3 skipped, 60 browser tests excluded.
  Formatting and scoped whitespace checks passed. Browser verification was not run for this
  server-side extraction.
- Independent review compared the extracted code with the removed handlers and checked
  relevant existing state/context and LiveView coverage; no scoped findings. The review
  assessed recorded test evidence without repeating the suite.
- Implementation is locally committed as `e9d5a13f34ccd04f704cd132ecc2b520faaecb60`.
  Next: obtain operator acceptance of the verified extraction and acknowledgement of the ruling below.
  Retire this handoff only after those gates. No publication is authorized.

Known limitation: existing blank rename submissions and validation can raise in
`TaskList.changeset/2` because Ecto coerces blank names to nil before `String.trim/1`.
This extraction preserves that behavior. Blank creation errors and duplicate rename errors
are covered; fixing the shared changeset would require a separate scope decision.

## Rulings awaiting acknowledgement

1. Proceed in the current session from the accepted extraction recommendation and explicit
   implementation request, without a second design/plan approval round. The recommendation
   fixed the boundary, dependencies, verification and scope. Cost if wrong: revise or undo
   the local reversible implementation if the operator wanted further design review first.
