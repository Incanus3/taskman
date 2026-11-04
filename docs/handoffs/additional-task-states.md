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
worktree, starting with the state catalogue/lifecycle deliverable (Task 72). No implementation
checkpoint has yet been verified or accepted.

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
The root and Task 72 are In Progress in Product; Tasks 73–78 remain Pending. The sibling
blocker chain is verified; Task 72 has no incoming blockers. Do not recreate this grouping.

## Remaining sequence and gates

1. Finish Task 72 with its red/green gates and independent scoped review; implementation is
   delegated and the root/child pickup transitions have been verified.
2. Continue Tasks 73–78 in the approved dependency order with independent scoped reviews and
   verified local commits. Update canonical product/API/CLI/help/completion/skill/spec owners.
3. Complete the integrated verification gates in the plan and record evidence, material limits
   and autonomous implementation rulings in their canonical owners.
4. Present the workstream for operator acceptance before retiring this handoff. Do not equate
   child review or tests with acceptance of the whole workstream.
5. Publication, merge and deployment remain separately authorized actions.

## Verification

No feature implementation checkpoint has yet been verified. Earlier `mix precommit` passed against the unchanged
product baseline: 1,398 passed, 3 skipped, 51 browser-tag exclusions. This does not establish
new-feature behavior. Design checks include source inspection and independent builder reviews.
Plan verification includes author coverage/interface/step/proportion review, independent readiness
review with no material gaps, and local-link, whitespace and scoped privacy checks. Backend reads
verified the new parent/child hierarchy, source markers, Pending/List placement and sibling blocker
chain. These tracker operations did not start implementation.

## Workstream rulings

No autonomous implementation rulings. All individual design decisions are accepted; builder
choices are identified in the specification. Final integration review follows the repository’s
scope rule: this session’s changes and relevant interactions.
