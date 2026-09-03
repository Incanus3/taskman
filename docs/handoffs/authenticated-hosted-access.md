# Authenticated Hosted Access Handoff

**Status:** active
**Updated:** 2026-09-03
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
- Completed delivery issue: `tas-authenticated-hosted-access-2a8.13`
- Completed CI compatibility issue: `tas-15n`

## Checkpoint

Tasks 1–13 are implemented and independently reviewed through commit `88b4e0e`, with the hosted
branch stacked on the design branch. Authenticated browser, API, administrator, account-management,
and CLI flows are complete; Accounts remains isolated in Ash while Project, List, and Task remain
Ecto. Production runtime configuration, release commands, systemd/Caddy examples, deployment and
rollback guidance, and integrated invited-user-to-disablement coverage are present. Delivery issue
`tas-authenticated-hosted-access-2a8.13` is closed.

The parent feature `tas-authenticated-hosted-access-2a8` and roadmap checkbox remain open because
the Caddy binary is unavailable locally. All locally available technical checks passed; the only
remaining acceptance evidence is host-side validation of `ops/caddy/Caddyfile`.

Alpine CI compatibility is restored: the workflow installs `build-base`, Accounts uses Ash's
pure-Elixir `simple_sat` fallback instead of the musl-incompatible PicoSAT NIF, and the Token
resource avoids compile-time expansion of its own struct under the CI toolchain.

## Next actions

1. On a deployment-compatible host with Caddy installed, run
   `caddy validate --config ops/caddy/Caddyfile`.
2. Record the successful validation evidence in the canonical roadmap/handoff state.
3. Close `tas-authenticated-hosted-access-2a8` and mark the roadmap item complete only after that
   evidence exists. Deployment and other external changes still require separate authorization.

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
- Do not mark the parent feature or roadmap complete until Caddy validation succeeds.
- Earlier scoped reviews left only non-blocking edge-coverage and compatibility minors; none block
  the host-side validation step.

## Verification

Before Task 1, `mix precommit` passed with 543 tests. Task 1 passed with 545 tests. Task 2 passed 8
focused Accounts tests, 82 Project/List/Task regressions, the migration-generation check, and
`mix precommit` with 553 tests on 2026-09-02. Independent reviews found no blocking issues.
Task 3 passed 16 focused tests after its terminal fix and `mix precommit` with 569 tests; scoped
re-review confirmed both production-terminal findings were addressed.
Task 4 passed 27 focused lifecycle/mail tests and `mix precommit` with 596 tests. Scoped re-review
confirmed transactional single-use, rotation, rollback, and genuine multi-connection contention.
Task 5 passed 23 focused auth/session tests, 223 LiveView tests, and `mix precommit` with 619 tests.
Scoped review confirmed all session hardening findings against the final immutable fix.
Task 6 passed 88 focused Accounts/API/CLI tests and `mix precommit` with 637 tests. Scoped review
confirmed complete-credential hashing, canonical verification, pre-parser rejection, persisted-user
authorization, exact expiry behavior, equal-length comparisons, and the narrow runtime CLI seam.
Task 7 passed 46 focused lifecycle/email/deletion/invitation/session tests and `mix precommit` with
654 tests. Scoped re-review confirmed transactional revocation and actor locks, locked-state email
routing, the unified Ash action, lifecycle guards, and genuine database-observed final-admin
contention.
Task 8 passed 12 focused settings/session tests, 65 broader auth/account tests, and `mix precommit`
with 666 tests. Scoped re-review confirmed repeated one-time-key replacement, current/pending email
state, active-session filtering, single deletion broadcasts, and deterministic change/reset races
that cannot yield an old-password-authenticated browser session.
Task 9 passed 21 focused admin tests, 75 related auth/account tests, and `mix precommit` with 687
tests. Scoped review confirmed server-blocked AshAdmin privilege events, persisted administrator
checks, safe User inspection and action forms, typed deletion confirmation, label-free exposure,
and normalized fallback-resistant record routing.
Task 10 passed 55 focused boundary tests, 76 related auth/API/configuration tests, and precommit;
a fresh full suite passed 707 tests. Scoped security review confirmed reset limiting before side
effects, resend guidance, strong distinct secrets, UUID-only log fields, remaining-window retry
guidance, loopback-only proxy trust, and secure production endpoint settings.
Task 11 passed 179 requested CLI/API tests and `mix precommit` with 740 tests; the escript reports
0.2.0. Scoped security review confirmed resolved-key-only Req authentication, descriptor-bound
config reads, exact modes and durable atomic writes, non-stealing token-owned writer locks, and
symlink-resistant installer updates with complete old/new skill visibility.
Task 12 passed 11 focused release/runtime tests, production compile/assets/release assembly, and
`mix precommit` with 751 tests. Scoped review confirmed migration/bootstrap endpoint isolation,
executable release overlays, Resend/Req runtime configuration, secret-safe errors, and validated
canonical sender addresses.
Task 13 passed its 2 integrated hosted-access tests, migration generation check, production
compile/assets/release assembly, staged-root systemd validation, and `mix precommit` with 753 tests.
Scoped re-review confirmed Caddy validation precedes first-install enable/start and later reloads,
with status/journal checks. Local Caddy validation was not run because the binary is unavailable.
CI compatibility issue `tas-15n` passed a clean compile in the exact Alpine CI image, a direct Crux
solve through `simple_sat`, 90 focused Accounts/Ash Admin tests, and `mix precommit` with 753 tests.
