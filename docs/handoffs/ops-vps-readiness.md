# Operations VPS readiness

Status: active. Updated: 2026-09-09. Resume: `$resume ops-vps-readiness`.

## Objective and authority

Prepare disposable-VPS acceptance after the completed static correctness pass and
documentation consolidation. Task: `tas-b7kd`.
The [deployment design](../specs/2026-09-09-dedicated-host-deployment-design.md) owns the final
architecture and constraints; the [runbook](../deployment.md) owns operator behavior,
recovery, and external acceptance gates. Simplification candidate-hunting is finished;
the accepted simplicity constraints still apply to any demonstrated correctness fix.

## Checkpoint

- The single deployment feature commit contains the correctness fixes,
  final design, retired historical plans, and pytest-owned test support.
- The approved package organization is applied locally: workstation helper packaging
  and invocation in `helper_client/`, release artifact modules in `releases/`, the
  scheduled adapter in `host_helper/`, and pyinfra support in `host/`.
- The approved packaging correction adds the missing release manifest and error modules
  to the transient archive only. Both zipapps are tested with `-I -S` so editable
  site packages cannot hide missing dependencies. Independent scoped review found no
  remaining material issues and reproduced all 805 operations tests plus isolated execution.
  The relocation-induced host-package import cycle was corrected by removing unused
  eager reexports. The operator authorized folding the reviewed changes into that commit.
- Fresh verification: 805 operations tests, 43 focused package/deploy/backup tests,
  and 802 application tests through `mix precommit` pass. Byte-compilation, shell,
  deploy help, and local links pass. Clean-source Docker release build and verified
  exact-input cache reuse passed for source `371ab5e8bb752f8abdb8ae38f5c4c62b08ae1f8c`
  (the same tree as feature commit `060b0d8d`, before this evidence-only amendment).
  Local checks do not establish real-host acceptance.
- The operator has a disposable VPS, but exact target/access and permitted external
  changes remain unconfirmed. No host action or merge has been performed.
- The approved follow-up consolidation moves the unchanged verification-request
  constructor into `host_helper/verification.py`, with consumer imports, archive
  membership, tests, and the design map updated. It is included in feature commit
  `6a480edb`; the release build above predates this change.
- The approved test-only split remains uncommitted: authenticated access stays in the
  web suite, rendered systemd contracts live in the operations suite, and release
  hardening has focused Elixir coverage. Existing Caddy coverage is reused; brittle
  runbook prose assertions were removed. Fresh verification: 805 operations tests and
  804 application tests through `mix precommit` pass; scoped independent review found
  no material issues. No production behavior changed.

## Next actions

1. Prepare the disposable-host checklist; deployment must resolve an artifact for its
   selected source revision rather than assuming this earlier verification artifact matches.
2. Transfer VPS acceptance to a clean session, as requested by the operator. Confirm
   target, access, and external action scope there; do not infer historical staging DNS.
3. Execute only the authorized acceptance scope. Publication and merging require
   separate authorization after acceptance.
