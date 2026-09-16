# Operations VPS readiness

Status: active. Updated: 2026-09-16. Resume: `$resume ops-vps-readiness`.

## Objective and authority

Prepare the dedicated-host controller for merge by simplifying the operations implementation within
its accepted reliability boundary, reducing redundant or disproportionately slow tests, consolidating
branch history, and completing clean staging provisioning/deployment acceptance. This handoff does
not authorize a host reset, provider/DNS change, deployment, history rewrite, push, or merge.

- Canonical behavior: [reconciliation specification](../specs/2026-09-09-deploy-reconciliation-design.md),
  [dedicated-host design](../specs/2026-09-09-dedicated-host-deployment-design.md), and
  [operator runbook](../deployment.md).
- Parent tracking issue: `tas-sr4b`. Local final-verification tasks `tas-sr4b.11` and `tas-6dkg`
  are closed; the parent remains open for pre-merge hardening and external acceptance.
  Parallelism investigation `tas-sr4b.13` is closed; xdist adoption was accepted on 2026-09-16.
  Optimization task: `tas-sr4b.14` is complete (package reuse/history split verified; startup
  measured and explicitly deferred to step 3 on 2026-09-16).
- Audit and ordered candidate review: `tas-sr4b.16` is in progress; the register is recorded,
  candidate 1 is complete in `tas-sr4b.17`; candidate 2 is complete in `tas-sr4b.18`, committed as `d4335ffe`;
  candidate 3 producer correction is complete in `tas-sr4b.19`, committed as `664926a7`; translator removal
  and candidates 4–8 await disposition. Approved remaining design: `tas-sr4b.20`; proposed implementation plan: `tas-sr4b.21`.

## Current checkpoint

Latest planning checkpoint: `67c44511133ca14a799f206f3baf633ca9595027` on
`dedicated-host-deployment-automation`; design approved, reviewed implementation plan awaiting
operator approval. Earlier implementation evidence follows. This checkpoint includes
the verified package/history optimizations and report-validator simplification; its preceding tip
was `42ea507651206c7bab706486c15878608619f127`. Package reuse is implemented and independently
reviewed: immutable worker-local bytes/metadata, private installer copies, fresh packaged execution;
production builders and packaging/source-mutation coverage stay uncached.

The operator approved and implementation completed the history coverage split. The packaged
public deploy/restore test now uses three selections with oldest-only backup authority outside
the final-two projection; all public outcome/retention/bounded/no-export assertions remain.
Both real >4,096 observer regressions are unchanged. This supersedes the unaccepted direct-write
fixture proposal; no configurable limit or serializer was introduced. Independent task review
passed without findings. Canonical coverage allocation is in the reconciliation specification;
[measurements and disposition](../research/2026-09-16-operations-test-parallelism.md) own the evidence.

Focused integration timing dropped from 14.42 to 4.96 seconds; the two >4,096 observer cases passed
in 1.23 seconds. Two complete four-worker suites passed 1,463 tests with 158 warnings in 55.21
and 54.65 seconds, roughly unchanged from the package-reuse checkpoint's 55.01/55.20 seconds.
Retain the split for focused cost reduction and clear test ownership; no extra full-suite wall-time
saving is established. Locked sync, compileall, shell syntax and Markdown checks passed;
`mix precommit` passed 805 tests in 43.2 seconds. No external actions or history consolidation occurred.

The first production simplification (`tas-sr4b.15`) is complete; scoped independent review approved
runtime parity and closed its stale-documentation finding after follow-up. Controller verification
now uses the shared report validator directly. Fresh local gates:
1,464 operations tests passed in 52.01 seconds with 158 known warnings; `mix precommit` passed
805 tests in 42.9 seconds. Focused controller/protocol coverage passed 209 tests. Locked sync,
compileall, shell syntax, verify help and scoped documentation/terminology checks passed.

The broader step-3 structural audit is now recorded in the
[ranked candidate register](../research/2026-09-16-operations-simplification-candidates.md),
against clean tip `437cb1d217dfa39784436c3c3ebc93b269ff9160`. The operator approved candidate 1,
neutral migration-version extraction, and it is implemented locally in `tas-sr4b.17` with distinct
scoped review approval, committed as `15602d43` before candidate 2 at operator request. Candidate 2
is implemented, verified and committed as `d4335ffe`; candidate 3 producer correction is approved;
translator-removal design is now approved; its plan approval and candidates 4–8 remain pending. The register owns
evidence, boundaries, ranking rationale, trade-offs and proposed verification. A synthetic local
probe also established mutation-evidence degradation through invalid-result translation; candidate 3
retains its required failure-policy analysis. No production/test implementation or external action
occurred during the audit.

The shared filename/version extractor now serves record, manifest and fingerprint consumers;
the database parser is removed. Supported valid behavior and record error/limit boundaries remain.
Duplicate timestamp aliases now refuse earlier in provision planning, before confirmation or
convergence; the register records exact refusal semantics and accepted scope. Fresh gates passed:
1,492 operations tests in 59.06 seconds with 158 known warnings; `mix precommit` passed 805 tests in
43.0 seconds; locked sync, compileall and shell syntax passed. Focused coverage passed 496 distinct
tests including both actual isolated archives. No host action or history consolidation occurred.

Candidate 2 narrows SOPS to one callable/CompletedProcess binary contract, removes alternate result
conversion and dispatch, and preserves fixed secret refusal, redaction and mutable capture cleanup.
Distinct scoped secret-handling review approved without blocking findings: 35 tests and five extra
boundary checks passed independently. Fresh local gates passed 1,514 operations tests in 62.92 seconds
with 157 known warnings and 805 tests through `mix precommit` in 44.1 seconds. Locked sync, compileall,
shell syntax and scoped checks passed; 94 focused cases passed. Candidate 3's failure-policy design
is now approved, with plan approval pending; the earlier producer correction is completed below.

The producer correction is now verified locally in `tas-sr4b.19`: canonical deploy/genesis
`inspection`/5 results retain operation-owned mutation/report/backup/warning/final-fact evidence
without translation or entrypoint reinspection. A narrow validator correction admits that status
alongside existing inspection/8; other categories remain restricted. Distinct scoped review passed
16 regressions and 224 category combinations without findings; 139 focused cases passed. Fresh
gates: 1,530 operations tests in 58.67 seconds with 157 known warnings; `mix precommit` passed 805
tests in 42.8 seconds; locked sync, compileall, shell syntax and scoped/documentation checks passed.
The producer correction is committed as `664926a7`; remaining translator removal awaits approval
of the reviewed implementation plan below.

## Translator-removal design checkpoint

The operator approved the design on 2026-09-16 after discussing risks and trade-offs. The approved
[exact helper failure design](../specs/2026-09-16-exact-helper-failure-design.md) owns the
in-process evidence policy, report precedence, one final observer, encoding fallback, additional
service-stop producer correction and fixture/verification map. Distinct scoped design review
closed two corrected findings with no remaining blockers. Markdown/gate checks passed; fresh
`mix precommit` passed 805 tests in 43.4 seconds. No implementation is authorized.
Next: approve or revise the [implementation plan](../plans/2026-09-16-exact-helper-failure.md).
Its order is producer/fixture correction, shared cleanup validation, coupled recovery/encoding,
then integrated acceptance; commit each verified checkpoint. Distinct scoped plan review approved
without blockers; fresh `mix precommit` passed 805 tests in 43.7 seconds. Plan approval is pending.
After plan approval, resume in a fresh session before delegated implementation.
Deferring retains the translator and tracked stop-category defect; candidates 4–8 still require
ordered explicit disposition. Steps 4–6 and all external gates below remain unfinished.

## Immediate next increment

Step 2 of the agreed pre-merge sequence is complete. Package reuse and the history split are
verified; the operator explicitly accepted the measured startup disposition and deferred import
changes to pre-merge step 3. Do not revive the earlier approval gates, direct-write proposal or
unselected selective-import experiment.
The approved verification simplification is complete in `tas-sr4b.15`. The bounded inspection
that selected it preceded the broader audit, now recorded in the linked register. No startup change,
mutation-adapter removal or implementation beyond candidate 3's producer correction is approved.

1. Review the linked candidate 3 implementation plan and obtain approval before implementation. The register owns both proposed increments;
   the earlier producer correction is delivered and the remaining design is approved, with plan approval pending. Continue the remainder of candidate 3 and candidates 4–8 in recorded order,
   retaining each explicit disposition and implementing only its approved bounded design.
   The audit, ranking and candidates 1–2 delivery are recorded; remaining increments require approval. Follow the register's
   evidence and verification constraints, including candidate 3's approved failure policy and pending plan gate.
2. Use the current startup measurements if simplifying imports: both entrypoint and harness eagerly
   import the graph; selected read imports have a smaller closure, mutations retain most of it.
   No end-to-end import saving is established. Native-effect seams are inventoried in the research.
3. Preserve fresh children, actual archive entrypoint, `python -I -S`, readiness/timeout cleanup,
   fresh authority and every consumer assertion. No startup design is approved yet. Consider
   production lazy imports during later simplification without an indirect dispatcher justified
   only by unmeasured test savings. Fresh focused checks passed: historical-authority case in
   4.67 seconds; failed deploy retry and successful restore in 8.38 seconds. Production/test sources
   remain unchanged by this investigation; full-suite timing remains the preceding checkpoint.
   Final `mix precommit` passed 805 tests in 44.5 seconds after starting the stopped local database.

Startup investigation is complete; import changes remain a step-3 consideration.
Retain lost-genesis replay/changed-target refusal outcomes. The accepted history split loses direct
combined >4,096 packaged-consumer coverage; focused observers retain real large-history evidence.

Production simplification follows this investigation under canonical operations-development
guidance. Host acceptance and history consolidation retain their separate authorization gates.

The old staging installation is outside the supported format/runtime boundary. Its dated DNS,
email, administrator, and failed-upgrade observations are historical evidence only. Do not migrate,
repair, or run the new controller against it.

## Agreed pre-merge sequence

This is the agreed workstream order; checkpoint updates must preserve it and its gates.
We are now at step 3, candidate 3 implementation-plan approval: candidates 1–2 are committed, the producer
correction is committed and verified, the broader
audit/register is recorded; package reuse and
the approved history coverage split are verified, and startup investigation has its accepted
measured disposition. Candidate review/approved simplification and steps 4–6 remain unfinished.

1. **Profile the current operations suite.** Map overlapping coverage without deleting tests.
   Use measurements to identify expensive fixtures, repeated production seams and duplicated
   state transitions. Existing measurements are in the linked research document; refresh them
   when the relevant workload changes.
2. **Apply coverage-retaining test optimizations.** Reduce repeated builds, package assembly,
   subprocess work and filesystem setup; improve safe parallelism where measurements justify it.
   Keep the broad regression net intact. The explicitly approved history coverage split retains
   real >4,096 observer regressions and public packaged backup-authority assertions; it is not
   authorization for general test removal. Complete: startup was measured and import changes
   explicitly deferred to step 3.
3. **Review and simplify the ops implementation** under
   [Operations development](../development.md#operations-development), in this order:
   audit the current production implementation for simplification and other architecture-improvement
   candidates; record candidates with enough context in a durable repository file; rank them from
   highest expected return to lower, weighing benefit, implementation/maintenance cost and risk;
   then review each candidate with the operator in that order and implement only after its explicit
   approval. Record each candidate's affected boundaries, evidence, proposed change, behavior/safety
   trade-offs, verification and disposition so the process can resume outside the selecting session.
   The [register](../research/2026-09-16-operations-simplification-candidates.md) is now created;
   retain unreviewed candidates and pending decisions.
   The approved `tas-sr4b.15` increment is complete and precedes this broader audit;
   it does not establish that the remaining implementation has been audited. Read complete relevant
   specifications before proposing changes to accepted behavior. Preserve current operator
   behavior, destructive-target controls, backup/reference safety, migration compatibility, secret
   protection, truthful failure evidence and supported interruption recovery. Prefer safe refusal
   over machinery for unsupported theoretical combinations. Verify each bounded change against
   the existing comprehensive suite.
4. **With production structure stable, review test overlap and remove only proved duplicate
   coverage.** Retain focused public-boundary tests and one clear owner for each consequential
   invariant. Rerun complete local gates and clean/dirty build/package checks.
5. **Agree on a reasonable set of durable commit groups and squash with GitButler.** History
   rewriting and any required remote update need explicit operator authorization. Release identity
   embeds the source revision, so rebuild and repeat identity-sensitive local gates after squashing.
6. **Run clean real-VPS provisioning/deployment acceptance against the exact post-squash head.**
   Obtain the separate external authorization below first. Do not change production code after
   acceptance without rerunning the affected local and host gates.

## External gate — authorization required

Before acting, obtain explicit operator authorization identifying the disposable target, permitted
host/provider/DNS changes, access path and destructive restore scope. Recreate a clean supported
staging host and follow the [runbook](../deployment.md):

1. Provision twice; inspect HTTPS, HSTS, listeners, firewall and reboot behavior.
2. Perform administrator/login, invitation email, API key and LiveView acceptance.
3. Exercise a second release, rollback, forward deployment, controlled migration failure, backup
   and destructive restore.
4. Inspect canary-secret and release-cookie leakage before recording external acceptance.

Native PostgreSQL, systemd PID 1, UFW, DNS/ACME, email delivery, reboot and destructive restore
remain unproven by local fakes, packages or builds. Do not run the new controller against the
unsupported historical staging installation.
