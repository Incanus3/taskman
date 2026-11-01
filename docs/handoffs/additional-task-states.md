# Additional Task states

Status: active
Updated: 2026-11-01
Resume: `$resume additional-task-states`

## Objective and authority

Implement contextual visibility for additional Task states, introducing Not Planned and separating
closed-state counting from blocker resolution. The [design specification](../specs/2026-11-01-additional-task-states-design.md)
owns requirements, decisions, source evidence, boundaries and verification criteria.

## Current checkpoint

Behavioral decisions D1–D6 are operator accepted in discussion. The full written design, including
proposals D7–D8, awaits approval. Product code is unchanged. Source inspection confirmed that
Will Not Do currently resolves blockers; the operator explicitly requires changing that behavior.
Only Done will resolve blockers. A fresh read-only builder check found no consequential unanswered
questions after inspecting the specification against the relevant existing code boundaries.
The operator subsequently clarified D4: selectors and Task status sorting must share the canonical
sequence in the specification, with descending sorting reversing it. The draft's separate sort
ranking was rejected; the specification and verification criteria now reflect that correction.
Proposals D7–D8 now include shared color tokens and delayed description tooltips on state options.
Existing tooltips use a 450 ms hover delay but need opt-in active-option, accessibility and
top-layer support; ordinary tooltip consumers should retain their current behavior. These revised
details await full design approval. The read-only reviewer also checked these revisions and the
unified sorting contract, finding no consequential unanswered questions.

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

No autonomous implementation rulings. Specification proposals and builder choices are identified
in the design and remain subject to full design approval.
