# Additional Task states

Status: active
Updated: 2026-11-04
Resume: `$resume additional-task-states`

## Objective and authority

Implement contextual visibility for additional Task states, introducing Not Planned and separating
closed-state counting from blocker resolution. The [design specification](../specs/2026-11-01-additional-task-states-design.md)
owns requirements, decisions, source evidence, boundaries and verification criteria.

## Current checkpoint

The complete [design](../specs/2026-11-01-additional-task-states-design.md), including D1–D13,
is operator approved. The [implementation plan](../plans/2026-11-04-additional-task-states.md)
is operator approved, author-self-reviewed and independently reviewed with no material gaps.
The clean-session boundary has been crossed. Implementation is underway in the existing feature
worktree. The state catalogue/lifecycle deliverable (Task 72) is implemented at `5f54b091`
and independently accepted with no findings. Compact event/bootstrap work (Task 73) is accepted
at `f890b632`. Shared location ownership (Task 74) is accepted at `33fc77c9`. Contextual
filters and mutation/reconnect observations (Task 75) are accepted at `9accc530` plus correction
`bfac6854`. The in-picker reveal action (Task 76) is underway.

The plan carries the accepted scopes and ordering: Not Planned/only-Done blocker semantics,
compact Task event snapshots, one shared location owner, contextual filter preferences, picker
reveal and delayed descriptions, then external parity and integrated verification. Reveal persists
across Task navigation within the same edit dialog; native fallback offers all real states.
Location observations are page-local and additive; event handling adds no availability Task read.
Resnapshots run at known lifecycle boundaries, without missed-event detection. Accepted publication
and ephemeral-delivery limitations remain in the design.

Earlier independent design checks found no consequential gaps. Source inspection informed the
plan's explicit safeguards for persisted results versus unsaved autosave drafts, Project-ID-based
reconnect recovery after browser URL cleanup, generic action/value separation and badge click-through.

[Related notification-read research](../research/2026-11-01-notification-read-amplification.md)
is tracked separately through the reference recorded there. It does not expand this feature's scope.

## Tracker and immediate continuation

Workstream Task **71** is relative to this repository's registered Taskman Project. Verify its
exact description marker: `Source: Incanus3/taskman:additional-task-states`.
Children **72–78**, their exact markers and dependency order are recorded in the approved plan.
The root and Task 76 are In Progress in Product; Tasks 72–75 are Done after independent review;
Tasks 77–78 remain Pending. The sibling blocker chain is verified; Task 76’s prerequisite is Done. Do not recreate this grouping.

## Remaining sequence and gates

1. Finish Task 76: generic picker action/subset, reveal lifetime and browser value/focus checks.
2. Continue Tasks 77–78 in the approved dependency order with independent scoped reviews and
   verified local commits. Update canonical product/API/CLI/help/completion/skill/spec owners.
3. Complete the integrated verification gates in the plan and record evidence, material limits
   and autonomous implementation rulings in their canonical owners.
4. Present the workstream for operator acceptance before retiring this handoff. Do not equate
   child review or tests with acceptance of the whole workstream.
5. Publication, merge and deployment remain separately authorized actions.

## Verification

Latest gate after the Task 75 review correction: `ERL_FLAGS='+S 8:8' mix precommit`
passed with 1,444 passed, 3 skipped and 51 browser exclusions. The focused correction gate
passed 60 tests. A real rendered poisoned-source regression proves hierarchy filter opening,
closing and expansion use prepared projections without SQL/traversal; navigation refreshes the
selected breadcrumb. Independent re-review accepted both spec and quality with no findings.

Earlier accepted children establish safe migration rollback, canonical counts/sorting and
only-Done blocker behavior, compact snapshots/bootstrap and shared topology. Evidence and scoped
review results are on Tasks 72–75. Assets build passed before the projection-only correction.
Browser acceptance and remaining integration are pending.

The host’s default 72 async test cases exceeded the configured database pool of 16; use the
command-local scheduler bound above (16 cases), without changing repository test configuration.
The existing local development PostgreSQL container was started to enable tests.

## Workstream rulings

1. **Superseded; acknowledgement pending:** Initially interpreted “no hierarchy traversal on
   filter opening” as availability discovery only, retaining existing render-time row/path
   projection. Rationale: availability was the feature’s performance scope. Cost if retained:
   filter opening would still traverse the source tree. Independent review found this weaker
   than the literal specification and identified a bounded remedy; replaced by ruling 2.
2. **Current; acknowledgement pending:** Cache existing visible-root and selected-location-path
   projections when their inputs change, so filter opening/rendering performs no source-tree
   traversal. This follows the specification literally without a broader presentation rewrite.
   Risk: missed cache dependencies could produce stale rendering; focused regression coverage
   and independent re-review were completed successfully before Task 75 closed.
