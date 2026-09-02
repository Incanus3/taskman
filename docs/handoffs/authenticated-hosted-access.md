# Authenticated Hosted Access Handoff

**Status:** active
**Updated:** 2026-09-02
**Resume:** `$resume authenticated-hosted-access`

## Objective

Deliver authenticated browser/API access and an operable OTP release for Taskman's shared workspace
without migrating existing domain resources to Ash.

## Durable references

- [Authenticated hosted access specification](../specs/2026-09-02-authenticated-hosted-access-design.md)
- [Authenticated hosted access implementation plan](../plans/2026-09-02-authenticated-hosted-access.md)
- [MVP product specification](../product/mvp-spec.md)
- [MVP roadmap](../planning/roadmap.md)
- Beads feature: `tas-authenticated-hosted-access-2a8`
- Next issue: `tas-authenticated-hosted-access-2a8.1`

## Checkpoint

The specification and implementation plan are approved. The thirteen-task Beads delivery graph is
created under `tas-authenticated-hosted-access-2a8`, has no dependency cycles, and starts with the
AshPostgres/Ecto compatibility gate. Plan review strengthened the existing CLI-skill parity
contract: CLI `0.2.0`, canonical source, compiled bundle, installed copy, registry coverage,
examples, and ownership metadata must remain aligned without hard-coding a release version in
parity tests. No implementation has started.

## Next actions

1. Stop at this clean-session boundary.
2. Resume with `$resume authenticated-hosted-access`.
3. Read the approved spec and plan, claim `tas-authenticated-hosted-access-2a8.1`, and execute its
   test-first compatibility gate.

## Constraints and uncertainty

- Ash is isolated to Accounts in this workstream; Project, List, and Task contexts stay Ecto.
- Later domain work must not create a permanent hybrid; full Ash migration is separate.
- Account deletion permanently removes the user and dependent credentials while preserving the
  unowned shared workspace; it cannot delete the final active administrator.
- An administrator may change and explicitly confirm another user's email. Pending-user changes
  immediately rotate and resend the setup invitation rather than starting an email-change flow.
- Authenticated password changes rotate and preserve the acting browser session while revoking
  other sessions; recovery-link resets revoke every session.
- Actual server, DNS, Resend-account, and deployment changes remain unauthorized external actions.
- The first implementation gate must prove unchanged Ecto behavior after adopting
  `AshPostgres.Repo`.

## Verification

The specification was checked against the clean upstream baseline
`7a0a664caa6c2ef050649b98202df6db8c1ee415`. Documentation self-review found no unresolved
placeholders, contradictions, or broken local links after the account-deletion and administrator
email-management amendments and the password-change session correction. The implementation plan
was checked for specification coverage, file/interface ownership, task ordering, test-first steps,
local links, and whitespace. The Beads graph reports no dependency cycles and is synchronized to
JSONL. `mix precommit` passed with 543 tests on 2026-09-02.
