# Projects without primary directories

- Status: active
- Updated: 2026-09-10
- Resume: `$resume projects-without-primary-directories`

## Objective

Remove the machine-specific `Project.primary_directory` concept across persistence, browser, API,
CLI, and current product documentation without making replacement agent-integration decisions.

## Durable references

- Design: [Projects without primary directories](../specs/2026-09-10-projects-without-primary-directories-design.md)
- Beads work: not yet created; create it with the implementation plan

## Current checkpoint

The design direction is approved and the self-contained specification is written. A Project becomes
a machine-independent logical container with a name and no directory. Agent integration is deferred
rather than redesigned around Auggie or Orca.

## Next actions

1. Obtain review approval for the written specification.
2. Write the implementation plan and create the corresponding Beads work items.
3. Update this handoff, then resume implementation in a clean session.

## Constraints and uncertainty

- The database change is forward-only at the domain level; rollback requires restoring the matching
  pre-migration backup with the old release.
- The existing `dedicated-host-deployment-automation` stack is unrelated; keep this work on its own
  GitButler branch.
- No agent provider, pickup, launch, or local-checkout model is selected by this work.
