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
`bfac6854`. The in-picker reveal action (Task 76) is accepted at `ba5caabf` plus focus
correction `31ab1620`. Delayed descriptions (Task 77) are accepted at `6f3419e3`. External
parity, canonical documentation and integrated verification (Task 78) are accepted at `b2ab2550`.
All seven deliverables are implemented and independently accepted; final session integration
review and operator acceptance remain.

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
The root remains In Progress pending final integration review; Tasks 72–78 are Done after
independent review. The complete sibling dependency chain is satisfied. Do not recreate this grouping.

## Remaining sequence and gates

1. Complete independent review of this session’s integrated implementation and resolve findings.
2. Retire the completed implementation plan after harvesting remaining durable conclusions and
   updating its references. Record the final evidence and present the root In Review.
3. Obtain operator acceptance of the workstream and explicit acknowledgement/revision of the
   autonomous rulings below before marking the root Done or retiring this handoff.
4. Publication, merge and deployment remain separately authorized actions.

## Verification

Integrated frozen-source gates passed: domain/UI 741 tests, API 100 tests, CLI 299 tests
(three rebuilt-executable skips), `ERL_FLAGS='+S 8:8' mix precommit` 1,452 passed/3 skipped/60
browser exclusions, executable build, all three rebuilt-executable smoke tests with zero skips,
and all 29 required headed browser tests. Independent final-deliverable review inspected actual
successful logs and accepted both spec compliance and quality with no findings. Evidence and
scoped reviews are recorded on Tasks 72–78.

Actual CSS viewports were 1440×900, 390×844 and 320×400 with matching document scroll widths;
fresh screenshots showed readable muted rows/focus and unclipped description tooltips. Hit tests
verified descriptions above picker/hierarchy popovers. Native closed control/all seven options
were inspected with enhancement hidden. Native expanded styling, fully JavaScript-disabled
behavior, physical touch and real screen-reader announcements remain unverified. Local results
do not establish deployment or the pinned release runtime.

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
