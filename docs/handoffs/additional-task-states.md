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
is written, author-self-reviewed and independently reviewed with no material gaps. Plan approval
is pending. Product code is unchanged; no feature implementation or new tracker decomposition has
started.

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

## Remaining sequence and gates

1. Obtain operator approval of the reviewed implementation plan; incorporate requested corrections.
2. After that approval, create/reuse the repository tracker's appropriate Product workstream
   grouping under its task policy; refresh this handoff and offer the clean implementation-session
   boundary. Continue here only if the operator explicitly chooses it.
3. Execute the approved plan in dependency order, with independent scoped reviews and verified
   local commits; update the canonical product/API/CLI/help/completion/skill and specification owners.
4. Present integrated verification evidence, material limitations and autonomous implementation
   rulings for operator acceptance before retiring this workstream. Do not equate child review or
   tests with acceptance of the whole workstream.
5. Publication, merge and deployment remain separately authorized actions.

## Verification

No feature implementation/tests have run. Earlier `mix precommit` passed against the unchanged
product baseline: 1,398 passed, 3 skipped, 51 browser-tag exclusions. This does not establish
new-feature behavior. Design checks include source inspection and independent builder reviews.
Plan verification includes author coverage/interface/step/proportion review, independent readiness
review with no material gaps, and local-link, whitespace and scoped privacy checks.

## Workstream rulings

No autonomous implementation rulings. All individual design decisions are accepted; builder
choices are identified in the specification. The complete design is approved; implementation-plan approval remains pending.
