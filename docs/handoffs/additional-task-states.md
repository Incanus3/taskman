# Additional Task states

Status: active
Updated: 2026-11-04
Resume: `$resume additional-task-states`

## Objective and checkpoint

The [design specification](../specs/2026-11-01-additional-task-states-design.md) owns the implemented
contracts and verification limits. Not Planned, only-Done blocker resolution, shared state
catalogue, compact persisted snapshots, shared Project locations, contextual filters/preferences,
in-picker reveal, delayed accessible descriptions and API/CLI parity are implemented.

All seven implementation children are independently accepted. Final session-scoped integration
review of `0f4144f..38a6b104` approved with no findings. The final implementation checkpoint is
`b2ab2550`; later local commits finalize documentation and continuation state. The completed plan
has been harvested into product/spec/CLI owners and removed. Tracker references retain its approved
historical revision. No push, merge or deployment was performed.

## Tracker and acceptance gates

Workstream Task **71** is relative to this repository’s registered Taskman Project; verify its exact
marker `Source: Incanus3/taskman:additional-task-states`. It is In Review for operator acceptance.
Children **72–78** are Done after independent review; their evidence remains in their threads.
Do not recreate the grouping or reopen completed work without operator direction.

1. Obtain operator acceptance of the implemented feature and explicit acknowledgement/revision
   of both autonomous rulings below. Acceptance confirms the workstream outcome; acknowledging
   rulings confirms the implementation interpretation and its correction.
2. After those gates and durable harvesting are satisfied, mark the root Done and retire this
   handoff/index entry together. Until then keep the verified baseline and pending decisions.
3. Publication, merge and deployment remain separately authorized actions. Refresh relevant
   target/remote state before any separately authorized publication.

## Verification and limits

Integrated frozen-source gates passed: domain/UI 741 tests, API 100 tests, CLI 299 tests,
`ERL_FLAGS='+S 8:8' mix precommit` 1,452 passed/3 executable skips/60 browser exclusions,
executable build, all three rebuilt-executable smoke tests with zero skips, and all 29 required
headed browser tests. Independent reviews inspected actual logs and accepted the artifacts.

Measured CSS viewports were 1440×900, 390×844 and 320×400 with matching document scroll widths.
Fresh screenshots showed readable muted rows/focus and unclipped descriptions; hit tests verified
top-layer placement. Native closed control/all seven options were inspected with enhancement hidden.
Native expanded styling, fully JavaScript-disabled behavior, physical touch and real screen-reader
announcements remain unverified. Local evidence does not establish deployment or the pinned release
runtime. Ephemeral delivery and enclosing-transaction publication limits remain accepted in the design.

The host’s default 72 async cases exceeded database pool 16; the command-local scheduler bound above
uses 16 cases without changing repository configuration. The existing local development PostgreSQL
container was started for testing.

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
