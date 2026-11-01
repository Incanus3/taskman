# Additional Task states

Status: active
Updated: 2026-11-01
Resume: `$resume additional-task-states`

## Objective and authority

Implement contextual visibility for additional Task states, introducing Not Planned and separating
closed-state counting from blocker resolution. The [design specification](../specs/2026-11-01-additional-task-states-design.md)
owns requirements, decisions, source evidence, boundaries and verification criteria.

## Current checkpoint

All individual decisions D1–D9 are operator accepted in discussion; full written design approval
is pending. Product code is unchanged. The specification owns all behavioral detail, including
only-Done blocker resolution, unified sorting, shared color tokens and delayed descriptions on
state options and main-table state badges.

The latest accepted revision replaces exact current-use availability with a page-local index of
observed additional states. Bootstrap once per Project load, maintain prepared direct/subtree/
whole-Project sets on relevant external events and successful local writes, and retain observations
until full reload/remount. Selector opening, List navigation and Include child Lists changes only
read prepared entries. Relevant source evidence: Task notifications are invalidations, only the
active Project is subscribed, and the publisher excludes itself. Revisit/reconnect snapshots cover
subscription gaps. Do not add persistent usage history or last-instance reference counts.

Independent builder checks, including the new index/lifetime contract against notification,
workspace, reconciliation and movement code, found no consequential gaps. Badge hover must still
preserve the existing pointer-event passthrough for click/tap Task navigation and add no keyboard
tab stops.

## Remaining sequence and gates

1. Obtain full design approval; incorporate operator corrections.
2. After design approval, write and review an implementation plan using the writing-plans skill.
3. Obtain plan approval. At that boundary, refresh this handoff and offer a fresh implementation
   session; continue in the same session only if explicitly requested.
4. Implement the approved plan, verify the scoped changes, and update canonical product contracts,
   API/CLI/help/completion/skill surfaces and affected documentation. Commit verified checkpoints
   locally; publication requires separate authorization.
5. Present verification evidence, outstanding limitations and autonomous implementation rulings
   for operator acceptance before retiring this workstream.

No implementation or feature tests have run. Design verification includes source inspection,
the independent builder check, and passing local-link, whitespace and scoped privacy checks for
the four changed documentation files. `mix precommit` passed against the unchanged product baseline:
1,398 passed, 3 skipped, 51 browser-tag exclusions. This does not establish new-feature behavior.

## Workstream rulings

No autonomous implementation rulings. All individual design decisions are accepted; builder
choices are identified in the specification. Approval of the complete written design remains pending.
