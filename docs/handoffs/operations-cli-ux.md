# Operations CLI UX

Status: active. Updated: 2026-09-09. Resume: `$resume operations-cli-ux`.

## Objective and authority

Make operations progress, plans, and final outcomes understandable without weakening output
safety or release authority. Design task: `tas-7ncz`; retained-failure-report follow-up: `tas-6dkg`.
The [proposed specification](../specs/2026-09-09-operations-cli-ux-design.md) owns the design;
the [deployment design](../specs/2026-09-09-dedicated-host-deployment-design.md) and
[runbook](../deployment.md) still own implemented behavior.

## Current checkpoint

- The operator approved the command/output proposal, including early bare-provision refusal on
  completed installations and preservation of exact original first-install artifact replay.
- The written specification is drafted; scoped independent review requested changes. Written-spec
  approval and an implementation plan remain pending. No UX code has been implemented.
- The triggering bare-provision run built a new source candidate and failed late against an
  existing installation. Standalone verification subsequently passed on the old selected release.
  Prior successful repeat provisioning used the same explicit artifact, not a changed checkout.
- The spec uses a thin safe pyinfra presentation adapter, an invocation-owned progress reporter,
  stdout/stderr separation, and read-only early metadata inspection. No raw secret/exception logs,
  guessed remote phases, implicit deployment, or PostgreSQL refactor.
- Source baseline: `027f44e3`. Documentation links/whitespace checks passed and the unchanged
  application passed `mix precommit` with 805 tests during drafting; these do not verify new UX.

## Next actions

1. Resolve the scoped architecture/security review recorded on `tas-7ncz`: non-throwing presentation
   callbacks without global hook changes, terminal-control sanitization, staging-only identity
   wording, pre-mutation failure outcomes, and bounded inspection responses. Also self-review the
   JSON error-form and interactive `create-admin` exceptions. No final written-spec approval has
   been given.
2. Ask the operator to review the finished specification, then write and review its bounded plan.
3. Update this handoff at the approved-plan boundary; continue implementation in a clean session
   by default. Keep `tas-6dkg` within the agreed failure-diagnostics scope.

No host changes are authorized by this design work. The separate
[VPS readiness workstream](ops-vps-readiness.md) owns host acceptance and exact artifact context;
the [PostgreSQL refactor](postgresql-host-python.md) remains parked.
