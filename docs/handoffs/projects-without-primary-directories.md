# Projects without primary directories

- Status: parked
- Updated: 2026-09-18
- Resume: `$resume projects-without-primary-directories`

Resume only after the current operations branch is merged and this workstream is selected.
Use its own dedicated branch from refreshed main; retain the written-spec review and plan gates below.

## Objective

Remove the machine-specific `Project.primary_directory` concept across persistence, browser, API,
CLI, and current product documentation without making replacement agent-integration decisions.

## Durable references

- Design: [Projects without primary directories](../specs/2026-09-10-projects-without-primary-directories-design.md)
- Beads work: not yet created; create it with the implementation plan

## Current checkpoint

The design direction is approved and the self-contained specification is written. A Project becomes
a machine-independent logical container with a name and no directory. Agent integration is deferred
rather than redesigned around Auggie or Orca. Written-spec review approval remains pending;
no migration or implementation exists. Product-document correction remains in scope. If ProjectLive
decomposition runs first, use its resulting workspace creation owner; neither workstream requires
the other. Document refresh grants no deployment authority.

## Next actions when selected after the operations merge

1. Obtain review approval for the written specification.
2. Write and review the implementation plan from the complete design; create repository-local
   Beads work items for implementation and verification units through `br`.
3. After plan approval, update this handoff, then resume implementation in a clean session.
4. Generate, rather than hand-name, the migration using the command specified in the design.
5. Implement the vertical change across domain, UI, API, CLI, bundled skill, tests and canonical
   documentation without selecting a replacement agent model.
6. Verify the migration and rollback limitation explicitly, then run the full completion gates.

## Constraints and uncertainty

- The database change is forward-only at the domain level; rollback requires restoring the matching
  pre-migration backup with the old release.
- The existing `dedicated-host-deployment-automation` stack is unrelated; keep this work on its own
  GitButler branch.
- No agent provider, pickup, launch, or local-checkout model is selected by this work.
