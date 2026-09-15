# Operations VPS readiness

Status: active. Updated: 2026-09-15. Resume: `$resume ops-vps-readiness`.

## Objective and authority

Implement the approved desired-target deployment reconciliation locally, then prepare separately
authorized clean staging recreation and fresh provisioning/readiness.

- Approved specification:
  [Desired-target deployment reconciliation](../specs/2026-09-09-deploy-reconciliation-design.md)
- Approved implementation plan:
  [Reconciliation delivery](../plans/2026-09-14-deploy-reconciliation.md)
- Operator workflow and acceptance gates: [Deployment runbook](../deployment.md)
- Parent issue: `tas-sr4b`; next task: `tas-sr4b.9`.

## Current checkpoint

Plan Tasks 1–8 are implemented and their Beads issues are closed. The current GitButler branch is
`dedicated-host-deployment-automation`; Task 8 was implemented from `43d3928a` through `07083125`
and passed independent review after three focused fix rounds.

Task 8 provides restore-specific inspection and native capacity admission, durable database
identity and creation-intent handling, same-backup retry and reapply recovery across recognized
database arrangements, scheduler-safe binding publication, completed-binding cleanup, and precise
mutation evidence across partial cleanup and later read-only failures. It preserves the original
database and later writes, refuses unregistered loaded databases without the required proof, and
does not implement unfinished-restore replacement.

The old staging installation remains historical evidence outside the supported record/runtime
boundary. Do not migrate, repair, or invoke the new controller against it. No host action, push,
merge, deployment, or publication has occurred or is authorized.

## Next action

Plan Task 9 (`tas-sr4b.9`), **Unfinished restore replacement and bounded safety copies**, is in
progress from `33bdbd71`. The complete approved specification, baseline design, plan, and development
guide were refreshed. Host replacement/admission/retention is the first bounded continuation; public
planning/confirmation and packaged recovery integration follow, then a distinct combined reviewer.
Refresh live implementation state before restarting either continuation.

Task 9 must add exact replacement intent, pending/third-target normalization, the abandoned-input
content exception, and bounded safety-attempt pruning. It must support replacement after a failed
swap without deleting the original, validate required safety copies, and remain bounded beyond 64
replacement attempts. Continue afterward in dependency order through `tas-sr4b.11`.

## Verification baseline and remaining gates

- Task 8 exact gate: 184 passed.
- Task 8 supplementary gates: backup protection 35 passed; packaged public restore 21 passed;
  package isolation 4 passed; host acceptance, facts, preflight, and aggregation 82 passed; Python
  compileall succeeded.
- Fresh `mix precommit`: 805 passed.
- Final Task 8 independent review: all scoped findings addressed with no new Critical, Important,
  or Minor regression.
- Real PostgreSQL/systemd/VPS behavior remains an acceptance-stage risk owned by later separately
  authorized verification. Task 11 owns whole-workstream, Docker build, and clean staging gates.
