# Dependency security release

Status: active. Updated: 2026-11-06. Resume: `$resume dependency-security-release`.

## Objective and authority

Update package dependencies and the pinned release toolchain before the authorized landing and
deployment. The operator explicitly included release toolchain upgrades. Additional task states
already landed through PR #49 as `2030dfdca79a75f1fbf04d8f6249e96a5a7ee17a`.
Deployment requires an explicit operator environment selection. Follow the
[deployment runbook](../guides/deployment.md); actual host state and receipts remain private.

## Current checkpoint

The dependency update is published through [PR #50](https://github.com/Incanus3/taskman/pull/50),
which owns current publication, CI and release-build evidence. Once merged, continue from its
refreshed main-branch result rather than reviving its implementation branch.
Hex packages were refreshed to current stable versions except the existing Cinder compatibility
pin. Assent is explicitly overridden to 0.3.2; the updated Hex audit reports no advisories or
retirements. Python lock updates include Pydantic, pydantic-core and typing-inspection.
The npm package, Heroicons and daisyUI are already current. Tailwind is updated to 4.3.4 and
CI to Elixir 1.20.5 / OTP 29.1.2 / Alpine 3.24.4 and PostgreSQL 18.6.

The [release toolchain contract](../specs/2026-09-09-dedicated-host-deployment-design.md#build-and-artifact-identity)
owns current pins and prior-toolchain compatibility. Runtime implementation and independent
compatibility review are complete. The full operations suite passed 2,809 tests, with 22 optional
tests skipped. The clean source checkpoint `8339f64e9867cd870c3d1450891450a1b42fc231` built
successfully with the new toolchain; archive, checksum, manifest and cache reuse were validated,
and both packaged credential-prompt tests passed against the extracted release.

## Remaining sequence

1. Check PR #50's current state. If still open, confirm hosted CI and land under the existing
   operator authorization. Production inputs are unchanged from the verified source build;
   retain explicit audit limitations.
2. Build the exact landed source if no validated matching artifact exists; preserve artifact,
   checksum, manifest and cache evidence in the PR and private controller records.
3. Confirm the operator-selected environment; inspect host/release/backup state and dry-run.
   The new task-state migration requires `restore-required`: older application code cannot read
   `not_planned` rows. Deploy the exact landed revision, then verify the selected release and health.
4. Record the outcome and remaining limitations; retire this handoff only after explicit completion
   and acknowledgement of the rulings below.

## Verification baseline and limitations

- Package update `mix precommit`: 1,473 passed, 3 executable smoke tests skipped, 60 browser tests
  excluded. All 60 browser tests passed across the initial 59-pass run and one successful
  virtual-display retry. The initial headed test inherited a Wayland launcher configuration that
  prevented window resizing; isolated X11 configuration resolved it without test changes.
  The rebuilt executable subsequently passed all three smoke tests. Focused CLI suite: 299 passed,
  three executable cases skipped before the separately rebuilt executable gate.
- `mix hex.audit`: clean after package update. `npm audit`: zero vulnerabilities.
- Clean release build, complete artifact validation, exact-input cache reuse, and two packaged
  credential-prompt tests passed. Python compileall and shell syntax checks passed. Full operations suite: 2,809 passed, 22
  skipped, 176 warnings (including existing gevent/fork and PTY warnings).
- `uv audit --locked` still flags Paramiko 4.0.0, CVE-2026-44405. A fixed Paramiko 5.x release
  exists, but stable pyinfra 3.10.0 requires Paramiko and types-paramiko below 5. This is a
  supported-resolution blocker, not absence of an upstream fix.
- Independent package/runtime review found no blocking defect. Prior/current manifests round-trip
  unchanged; all 12 cross-mixed field cases refuse. The browser suite exercised real LiveView
  connections after WebSockAdapter 0.7.1.

## Rulings awaiting acknowledgement

1. Override AshAuthentication 4.x's Assent constraint with patched Assent 0.3.2. Taskman configures
   password and API-key authentication; no application OAuth/OIDC provider uses the changed APIs.
   The upstream constraint protects against OAuth breaking changes. Reassess before adding any
   OAuth/OIDC provider. Wrong compatibility assumptions could break authentication integration.
   Sources: [Assent changelog](https://github.com/pow-auth/assent/blob/v0.3.2/CHANGELOG.md),
   [AshAuthentication constraint rationale](https://github.com/team-alembic/ash_authentication/issues/1097).
2. Retain the documented Cinder 0.17 compatibility pin and stable pyinfra dependency constraints.
   Cinder 0.18 requires hooks absent from current AshAdmin; overriding Paramiko 5 requires separate
   native SSH compatibility evidence because the controller uses pyinfra's private connector seam.
   The Paramiko SHA-1 advisory therefore remains visible; do not suppress or claim it fixed.
   Sources: [pyinfra requirements](https://pypi.org/pypi/pyinfra/3.10.0/json),
   [Paramiko changelog](https://www.paramiko.org/changelog.html).
