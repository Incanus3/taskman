# Missing List recovery

- Status: parked
- Updated: 2026-09-19
- Resume: `$resume missing-list-recovery`

## Objective

Stop location-bound Task actions when a selected List disappears and provide explicit recovery of
creation, detail, and movement input without silently saving, redirecting, or discarding it.

## Durable references

- Design draft: [Missing List recovery](../specs/2026-09-18-missing-list-recovery-design.md)
- Implementation plan draft: [Missing List recovery](../plans/2026-09-18-missing-list-recovery.md)
- Investigation evidence: [ProjectLive disappearance evidence](../specs/2026-09-03-project-live-decomposition-design.md#missing-location-investigation-evidence)
- Verified technical baseline: [ProjectLive decomposition design](../specs/2026-09-03-project-live-decomposition-design.md)
- Delivery tracking: `tas-1tq.10`; deferred increments `tas-1tq.10.1` -> `.2` -> `.3`

## Current checkpoint

The workstream is parked until the operator selects it for continuation. The operator approved the
recovery direction: stop pending and stale location-bound actions, expose
retained input in accessible recovery UI, and require explicit destination selection before creation
resumes, with copy and discard available. The bounded specification and three-increment plan are
drafted and passed scoped fresh-context contract review after route-completion ownership and exact
error semantics were corrected. Link, anchor, whitespace, placeholder, and internal-consistency
checks pass. Detailed design and plan approval remain required; no recovery implementation or
regression coverage has been authorized.

Three rolled-back test-database probes established that the current baseline can save stale creation
into a surviving parent-derived List, clear a hidden draft through pending detail autosave after Task
loss, and retain an inaccessible movement error. Fixture limits and evidence remain in the linked
design. There is no supported product List deletion mutation or established deletion notification
order.

Recovery depends on the verified extracted ProjectLive interfaces but is not part of the
decomposition workstream. Its planning artifacts are published in PR #17; implementation will
start later on a dedicated `missing-list-recovery` branch from refreshed `main`. Publication of the
drafts does not approve implementation, merge, or workstream completion.

## Remaining execution sequence

1. Current position: parked. Make no recovery changes until the operator selects this workstream.
2. On selection, refresh `main`, create the dedicated `missing-list-recovery` branch, review the
   complete recovery design and plan, resolve findings, and obtain explicit written-design and
   implementation-plan approval before adding regression coverage or source changes.
3. After approval, follow the approved clean-session boundary once unless the operator asks to
   continue in the current session. Execute in order: `tas-1tq.10.1` capture, suspension, and
   accessible recovery; `.2` explicit creation restoration; `.3` detail and movement restoration
   plus final verification. Each increment depends on its predecessor and requires focused
   verification and independent scoped review.
4. Publish and merge implementation only with their separate operator authorizations and refreshed
   target state. Obtain explicit recovery-workstream completion confirmation before closing its
   tracking issue and retiring this handoff.

## Pending decision

The proposed snapshot lifetime, parent relationships, navigation behavior, and exact recovery
controls require operator review and approval. Creation must resume only after explicit destination
selection; editing must distinguish surviving from missing Tasks; movement must reopen against fresh
Task and destination authority. List deletion, deletion-event vocabulary, schema changes, new
dependencies, and draft persistence across reloads remain outside scope.

## Workstream rulings

Recovery rulings are retained here in decision order. The decomposition design owns the lasting
extraction contracts and verification baseline.

1. **Current — draft specification and plan together:** Produce both reviewable drafts in this
    session following the operator's explicit request to continue approved planning. Do not treat
    direction approval as approval of the detailed artifacts or implementation. This replaces the
    staged specification-review pause before writing a plan. Cost if wrong: plan rework after written-design
    review; drafts are explicitly unapproved and no source changes are made.
2. **Current — separate continuation ownership:** Recovery has its own handoff and resume command;
   the completed decomposition supplies only its verified technical baseline. Existing Beads
   ancestry remains because `tas-1tq.10` depends on the extracted interfaces, but it does not make
   recovery part of the decomposition workstream. Cost if wrong: sessions could again conflate the
   completed extraction with recovery design or implementation approval.
3. **Current — park recovery for a dedicated branch:** Do not continue recovery on the decomposition
   branch. When the operator selects this workstream, refresh `main` and open the dedicated
   `missing-list-recovery` branch before continuing review or implementation. Cost if wrong:
   recovery changes become coupled to the completed extraction PR and blur approval and merge
   boundaries.
