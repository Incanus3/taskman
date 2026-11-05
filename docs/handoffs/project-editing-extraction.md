# Project editing extraction

Status: active. Updated: 2026-11-05.
Resume: `$resume project-editing-extraction`.

## Objective and checkpoint

Extract Project validation and submission from `Workspace` into a socket-free
`ProjectEditing` collaborator while preserving behavior. Implementation is complete and
independently reviewed without actionable findings. The boundary is recorded in the
[workflow decomposition specification](../specs/2026-09-03-project-live-decomposition-design.md#taskmanwebprojectliveworkspace).

`ProjectEdit` retains form state. `Workspace` applies explicit editing outcomes to modal state,
selection, page title, streams, and navigation. Fresh target checks before validation and
persistence, canonical persisted attributes, and draft retention are preserved.

## Verification and remaining sequence

- Seven new workflow tests were observed failing before implementation; the focused five-file
  gate then passed all 46 tests.
- Independent review compared all extracted branches with the original implementation and
  inspected the relevant tests; no actionable findings or autonomous scope rulings arose.
- `mix precommit --max-cases 16` passed: 1,459 tests passed, 3 skipped, 60 browser tests excluded.
  The initial default run failed 20 tests with database connection queue timeouts at 72 concurrent
  cases against a 16-connection pool. The bounded rerun passed without source or configuration
  changes. Browser verification was not run for this server-side extraction.
- The second-check disappearance and persistence-error paths were preserved by direct comparison;
  deterministic tests for these concurrent paths were not added.

Next: obtain operator acceptance of the verified extraction. The bounded changes are locally
committed. Retire this handoff only after explicit completion confirmation. Publication remains
separately authorized; no push or merge is requested.
