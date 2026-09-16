# Operations simplification candidate register

Status: candidates 1–7 complete; candidate 8 skipped by operator; production review complete. Updated: 2026-09-17.
Workstream: [Operations VPS readiness](../handoffs/ops-vps-readiness.md), completed pre-merge step 3.
Tracking: `tas-sr4b.16` (closed); parent `tas-sr4b` remains open for hardening and external acceptance.

## Scope and authority

This register records architecture and simplification opportunities in production operations code
at `dedicated-host-deployment-automation` revision
`437cb1d217dfa39784436c3c3ebc93b269ff9160`. The checkout was clean before the audit.
It is an ordered discussion agenda, not an approved specification or implementation plan.
Candidate 1's bounded design was approved and implemented on 2026-09-16; `tas-sr4b.17` owns delivery evidence.
Candidate 2 was approved and delivered after a commit-first checkpoint (`15602d43`); `tas-sr4b.18` owns evidence.
Candidate 3's producer correction and translator removal are approved and delivered in
`tas-sr4b.19` and `tas-sr4b.22`; the linked design and completed plan own the evidence.
Candidate 4 is approved and delivered in `tas-sr4b.23`. On 2026-09-17 the operator approved
candidate 5 bytes normalization first, then agreed to pursue single-plan ownership after separate
bounded-design approval. The operator subsequently approved that bounded single-plan design;
`tas-sr4b.25` owns the completed single-plan implementation and verification. Candidate 6
is approved and delivered in `tas-sr4b.26`; candidate 7 is approved and delivered in `tas-sr4b.27`; candidate 8 was skipped by the operator on 2026-09-17. The ordered production review is complete.
Any newly proposed implementation still requires an explicitly approved bounded design and refreshed
consumer/authority evidence.

The [development guide](../development.md#operations-development),
[dedicated-host design](../specs/2026-09-09-dedicated-host-deployment-design.md),
[reconciliation specification](../specs/2026-09-09-deploy-reconciliation-design.md), and
[runbook](../deployment.md) retain authority. Preserve operator commands and confirmations,
destructive-target limits, backup/reference protection, migration compatibility, secret protection,
truthful failure evidence, and supported interruption recovery. Unsupported ambiguous states may
refuse safely. Unused internal Python names need no compatibility layer; documented configuration,
wire schemas, persisted records, and generated host executables remain real contracts.

## Audit method and coverage

Repository-wide function/import and consumer inventories were combined with direct inspection of
the relevant procedures and their existing tests. The inventory contains 23,355 physical Python
lines; that is scope context, not a reduction target. A separate read-only discovery worker inspected
host admission, provisioning services, configuration/secrets, release construction, and transport;
the primary agent inspected and synthesized shared invariants, protocol/results, controller
orchestration, host observation/recovery, packaging, and the existing startup research.

| Area inspected | Conclusion shaping the register |
| --- | --- |
| Configuration, secrets, CLI and output | Unused internal aliases and permissive injected SOPS result forms are removable leads. Public aliases, redaction and status boundaries remain distinct. |
| Host facts and admission | Evidence collection and admission policy have different owners. The unused pristine-only admission entry point deserves test-to-production mapping. |
| pyinfra, PostgreSQL, Caddy, firewall and systemd | Test-only evidence allowances and asset-byte ownership are candidates. Native consequence checks and scheduler coordination remain justified. |
| Release source, build, manifests and resolution | Exact artifact identity, frozen dirty capture, drift checks, archive validation and installed/cache/build ordering implement concrete guarantees. No broader redesign selected. |
| SSH, native subprocesses and helper packages | Different execution domains justify separate runners. Deterministic allowlists, private staging, bounded I/O and exact cleanup remain. |
| Protocol and controller result integration | The completed shared report-validator change is excluded. The remaining invalid-result translator needs failure-policy analysis. |
| Host state, backups, protection, restore and cleanup | Full-history validation and finite recovery arrangements are necessary. Repeated migration extraction has a smaller neutral owner; generic recovery/retention frameworks are rejected. |
| Startup and test consumers | Existing import measurements constrain the last candidate. Test deletion belongs to step 4 after production structure stabilizes. |

This is a structural improvement audit, not exhaustive defect certification or a full-branch code
review. Cost, benefit and risk below are qualitative judgments grounded in consumers and consequence
boundaries; no runtime saving or line-count saving is promised. Existing tests were inspected, not
treated as proof that a proposed change preserves behavior.

## Ranked review order

Return means expected ownership/maintenance improvement relative to implementation and verification
cost. Small changes with clear shared consumers lead; larger safety-sensitive redesigns follow.

| Rank | Candidate | Expected benefit | Cost / risk | Disposition |
| --- | --- | --- | --- | --- |
| 1 | Give migration-version extraction a neutral owner | Clear shared invariant; fewer parsing variants and host database imports | Small / low to moderate | Completed 2026-09-16 (`tas-sr4b.17`) |
| 2 | Narrow the SOPS runner/result contract | Remove unused result forms and retry dispatch | Small to moderate / moderate, secrets boundary | Completed 2026-09-16 (`tas-sr4b.18`) |
| 3 | Retire old-shape mutation translation with truthful exact failure handling | Remove a second result interpretation path; address demonstrated evidence degradation | Moderate / high, failure evidence | Completed (`tas-sr4b.19`, `tas-sr4b.22`) |
| 4 | Make provisioning injection use the production evidence contract | Remove compatibility branches and implicit all-create authority | Moderate / high, resource/scheduler authority | Completed (`tas-sr4b.23`) |
| 5 | Give confirmed systemd asset bytes one owner | Fewer representations; bind validation and installation to the same rendered content | Moderate / moderate | Bytes-only and single-plan increments delivered (`tas-sr4b.24`, `tas-sr4b.25`) |
| 6 | Remove unused internal compatibility names | Smaller supported internal surface | Small / low | Completed (`tas-sr4b.26`) |
| 7 | Retire unused pristine-only admission after mapping its tests | One production path per admission capability | Moderate / moderate; small production reduction | Completed (`tas-sr4b.27`) |
| 8 | Consider plain selected-handler imports | Smaller read-operation import closure, if production becomes clearer | Moderate / moderate; end-to-end benefit unproved | Skipped by operator 2026-09-17 |

## 1. Neutral migration-version extraction

**Audit evidence and boundaries (before implementation).** [database.py](../../ops/taskman_ops/host_helper/database.py)
`release_migration_versions` is pure filename parsing beside PostgreSQL subprocess observation.
Production consumers include controller deploy/restore and host discover/deploy/restore/rollback.
[state.py](../../ops/taskman_ops/host_helper/state.py) `_release_migration_versions` separately slices
filenames and validates order. Host deploy `_migration_versions_from_manifest`, controller deploy
`_pending_migration_versions`/`_validate_migration_policy`, and provision `_provision_plan_effects`
also derive integer versions from filenames. [migrations.py](../../ops/taskman_ops/migrations.py)
already owns sorted unique non-negative integer sequences and is included in both zipapps.

**Proposed bounded change.** Add a pure filename-sequence extraction capability to `migrations.py`
and have these consumers project filenames into it. Keep mapping/record and typed-manifest adapters
explicit and small; preserve each boundary's accepted container shapes, error classes/messages,
and limits. Remove the old internal database-module parser after all consumers move. Do not move
database observation, fingerprint hash validation/agreement, source selection or recovery policy.

**Benefit, trade-off and alternatives.** One owner of filename grammar and version order reduces
drift without creating another configurable layer. A separate generic migration framework or
moving PostgreSQL code to neutral utilities would cost more. Retaining the duplicated parsers
avoids change risk but leaves actual shared invariant ownership fragmented. Supported valid migration
behavior remains unchanged; invalid history refusal is described below. No timing saving is established.

**Verification.** Extend `ops/tests/test_migrations.py` for valid/empty filename sequences,
malformed names and repeated/out-of-order timestamps, preserving boundary errors and record limits.
Run affected database/state, deploy/provision/restore/rollback tests and both packaged isolation
paths under `-I -S`. Consumer search must find no old parser imports. Complete local gates and a
distinct scoped review precede completion. No new compatibility shim or test removal.

**Delivered disposition.** `migrations.versions_from_filenames` now owns filename grammar and
sorted unique version extraction. Record mappings use the explicit `records.migration_record_versions`
adapter; typed manifests and fingerprint indexes project filenames directly. The old database parser
and its exports/imports are removed. Record container/error/limit boundaries, fingerprint hashes,
source choice, database observation and recovery policies remain local and unchanged. Existing safety
coverage was retained; 28 new tests cover extraction and boundary refusals.

Full grammar/order validation also tightens invalid histories. Record/manifest schema validation
ensures sorted unique filenames, which can still contain distinct names with the same timestamp.
Provision plan projection now refuses those duplicate versions before presenting/confirming a plan
or running provisioning; refreshed projection also precedes provisioning. The workflow closes the
remote and raises `MigrationOrderError`; the CLI maps it to `LOCAL_PREREQUISITE`, stage `controller`,
fixed message `controller operation failed`, `changed=False`. This replaces a possible dry-run plan
or later genesis refusal for an unsupported invalid history. It is accepted within the approved
sorted-unique-version/safe-refusal boundary, not described as unreachable raw-object behavior.
Directly constructed malformed typed objects also receive full grammar/order checks. State fingerprint
agreement and backup/genesis admission already validate version history before consequential use.

Verification and distinct scoped review evidence are recorded in the final section below.

## 2. Narrow SOPS runner/result contract

**Evidence and boundaries.** [secrets.py](../../ops/taskman_ops/secrets.py) `_result_parts` accepts
raw output, mappings, one/two/three-element tuples, `CompletedProcess`, and arbitrary attribute
objects. `_call_runner` accepts callable or `.run()` objects and retries `TypeError` with another
signature. Production provisioning calls `decrypt_secrets` with its default `subprocess.run`;
`ops/tests/test_secrets.py` uses a callable `CapturedRunner` returning `CompletedProcess`.

**Proposal and return.** Use one explicit callable, subprocess-shaped result contract. Remove
unused shape conversion and signature retry after inventorying every injected caller. This removes
real branches; sharing a generic subprocess adapter with other trust domains would add complexity.
Implementation is relatively small, but secrets handling warrants focused security review.

**Safety and verification.** Preserve decrypt statuses, malformed/empty YAML and UTF-8 refusals,
buffer cleanup, secret registration/redaction, and fixed error output. Callable failures must not
trigger an unintended second invocation. Exercise `test_secrets.py` and provision workflow tests,
including nonzero exit, runner exceptions and canary/buffer assertions, then complete local gates.
Do not replace `SecretConfig` or alter the encrypted document/runtime rendering contract.

**Approved bounded scope.** The operator approved this design on 2026-09-16 with a commit-first
condition; candidate 1 and audit artifacts were committed as `15602d43` with a clean workspace.
The fresh injection inventory contains only the callable `CapturedRunner` in secrets tests and
the default production decrypt capability in provisioning. Use `CompletedProcess` with binary
`bytes`/`bytearray` captures; `None` is empty capture. Text, memoryview and arbitrary capture values
need no coercion compatibility. Invalid captures still require fixed secret refusal and wiping any
mutable sibling capture. Injected callable failures receive no second invocation. `tas-sr4b.18`
owns test-first implementation, full local gates and distinct scoped secret-handling review.

**Delivered disposition.** The two private runner/result helpers and arbitrary output coercion are
removed. `decrypt_secrets` directly invokes the default subprocess or the injected callable once;
only `CompletedProcess` results and the approved binary captures are accepted. Both transport fields
are cleared and mutable captures wiped on success and supported refusal, including invalid sibling
capture cases. Unsupported result objects receive fixed refusal without generic mutation cleanup.
SecretConfig, rendering and unused aliases remain unchanged. Twenty-two new tests retain existing
coverage. The guarantee remains mutable capture wiping/reference clearing, not erasure of immutable
Python values. Full local and independent verification evidence follows below.

## 3. Exact mutation failures without old-shape translation

**Evidence and boundaries.** [host entrypoint](../../ops/taskman_ops/host_helper/__main__.py)
`_dispatch` validates deploy/genesis/restore/cleanup mutation results and sends invalid results to
`_legacy_mutation_result`. Those production handlers now emit exact protocol state. The translator
still reads old `changed`, `intended_release_id` and `final_observations` fields, remaps boundaries,
and may reacquire the lifecycle lock. Entry-point tests inject old shapes for history/report and
observation reuse. It is therefore incorrect to describe this as simply unreachable dead code.

A pure local probe, without native commands or a host, reproduced a consequential mismatch:
host deploy `_result(..., boundary="observation", changed=True)` emits
`changed` / `observation` / exit 5. The shared validator rejects that category. Passing it through
the translator with a substituted unavailable observer yields `unknown` / `inspection` / exit 8.
This demonstrates loss of proved mutation classification in this constructed result path; it does
not establish the frequency of the underlying native observation failure.

**Proposal and trade-off.** Make exact results the sole handler contract; fix producer category
inconsistencies, then replace translation with a small explicit invalid-result refusal/failure
boundary. Before implementation, resolve the correct observation failure category against the
accepted specification and protocol and design what independently valid mutation/report/completion
evidence survives malformed results. Blindly deleting translation or routing every failure through
the generic exception fallback would risk degrading proof and reports. No old handler shape needs
a compatibility reader, but its meaningful safety outcomes must retain coverage.

**Verification.** `ops/tests/host_helper/test_entrypoint.py`, host mutation tests, protocol result
tests and public packaged failures must cover exact history failure with a passing report, failed
verification reports, known change followed by malformed evidence, unavailable observation, lock
contention, inspect-only cleanup and completed cleanup targets. Preserve the existing no-success-
reinspection assertion. Run complete local gates and distinct correctness review. The [bounded failure design](../specs/2026-09-16-exact-helper-failure-design.md) now
defines the evidence policy and fixture migration; the operator approved it on 2026-09-16.
The [implementation plan](../plans/2026-09-16-exact-helper-failure.md) was approved on 2026-09-16 in `tas-sr4b.21`. It includes correcting the additionally discovered service-stop producer to
service/8 before translator removal. The bounded sequence is delivered in `tas-sr4b.22`; the operator explicitly chose execution in the approval session.

**Delivered producer correction (2026-09-16).** Split delivery so the demonstrated producer defect is
corrected before removing translation. First, host deploy's `CommandError` observation failure
must emit canonical `inspection` (accepted with exit 5), not unsupported `observation`. Preserve
the operation-owned mutation classification, final observation/availability, report, backup identity
and warning evidence. Verify the production failure branch and exact result through the entrypoint
without translation or a second observation, including a proved change and unavailable final facts.
Run scoped host/protocol/controller failure tests, complete local gates and distinct correctness review.
The operator approved this bounded producer correction on 2026-09-16; it is implemented and verified
locally in `tas-sr4b.19`. Translator removal is a separate increment now authorized through the approved plan.

Implementation discovery: the general protocol category table admits `inspection`/5, but its
deploy/genesis override only admits 8. Producing the approved valid exact `inspection`/5 state
therefore also requires narrowly accepting 5 alongside existing 8 for deploy/genesis inspection.
Selection/service/history remain 8; restore inspection remains 11 and cleanup remains 10. This is
necessary for the approved result contract, not a broader category change. Scoped protocol tests
must protect those exclusions, alongside the actual handler/entrypoint regression.

**Delivered translator removal (2026-09-16).** The separately approved design and plan
were implemented in `tas-sr4b.22`, after the producer correction. Exact handler results pass directly;
invalid matching results retain independent validator-approved proof, coherent primary outcomes
and report precedence. One validated observer replaces invalid final groups. Two encoding attempts
retain required proof without truncation; cleanup inspection remains unchanged with no completions.
Retired fields, aliases and translation are removed; exact fixtures retain consequential assertions.
Retaining duplicate interpretation/evidence loss or deleting translation before failure-policy design
remain rejected alternatives. The [design implementation evidence](../specs/2026-09-16-exact-helper-failure-design.md#implementation-evidence)
owns regression/review/size details. Four independent-review findings were fixed and re-reviewed
without remaining blockers. Final focused gate: 249 passed; integrated operations: 1,572 passed
with 158 known warnings in 58.77 seconds; `mix precommit`: 805 passed in 42.2 seconds.
Locked sync, compileall and shell syntax passed. No release-build or native-host acceptance follows.

## 4. Production-shaped provisioning evidence

**Evidence and boundaries.** [provisioning.py](../../ops/taskman_ops/provisioning.py)
`validate_existing_authority` accepts `None` explicitly for legacy capability tests even though
the real observer returns closed `ProvisionAuthority`. `ProvisioningInputs.scheduler_create`
defaults to all-create. [provision workflow](../../ops/taskman_ops/workflows/provision.py)
supports both an older artifact-only capability path and production `target_resolution`/`preflight`,
with permissive authority projection and scheduler-delta defaults. Production defaults use the
newer capabilities and pass the confirmed scheduler delta before convergence.

**Proposal and return.** Update injected workflow/capability fixtures to supply production-shaped
authority and make the production requirements explicit. Remove only compatibility paths with
no meaningful production consumer. This can simplify a large procedure and make missing authority
fail visibly, but test migration and safety review cost more than the earlier candidates.

**Safety and verification.** Absence of a resource/current/backup remains a meaningful supported
state; absence of authority is different. Preserve pristine and partial installation, credentials,
confirmed pre-convergence state, first-success replay, source-drift limits, mutation aggregation,
and scheduler create-only versus refresh ordering. Map all affected assertions in
`ops/tests/workflows/test_provision.py`, `test_pyinfra.py`, `services/test_systemd.py` and packaged
provision scenarios before removing paths. Complete local gates and distinct authority review.
Do not fold the separate proposed early-inspection CLI UX into this change.

**Approved bounded scope (2026-09-16).** The operator approved production-shaped target resolution,
preflight authority and explicit scheduler creation evidence after considering fixture setup and
migration costs. Remove the artifact-only injected workflow branch, `None` authority allowance and
implicit all-create scheduler behavior; migrate all consumers including packaged scenarios. Keep
existing safety assertions and map their coverage before editing fixtures. Preserve supported absent
resources, pristine/partial installation, credentials, confirmation/source-drift bounds, replay,
mutation/warning aggregation and locked genesis scheduler refresh. Missing evidence must fail safely
before mutation. Do not duplicate the host observer's closed schema or expand into systemd asset
ownership, early-inspection UX or general test removal. Focused red/green refusal evidence, complete
local gates and a distinct scoped authority review precede completion. No runtime saving is promised.
Delivery is tracked by `tas-sr4b.23`; candidates 5–8 and later pre-merge gates remain pending.

**Implemented boundary and assertion mapping.** Target resolution and preflight authority are
mandatory. The artifact-only resolver and `None` observer escape are removed. Preparation carries
an immutable empty scheduler-create set; only the confirmed explicit delta reaches pyinfra and
genesis. Both observation passes validate that delta through the existing `ProvisioningInputs`
owner before presenting a plan or converging. Convergence uses the initially validated inputs;
genesis still owns locked scheduler pause/wait/refresh/resume. The additional downgrade capability
binds the existing production classifier without adding a policy or alternative live path.

Missing authority, missing scheduler evidence and unprojectable resource discovery refuse safely.
Missing consumer fields and ordinary protection/reference `RecordError` failures are normalized
locally to fixed safety results. Observer cleanup warnings are retained before validation, and
refreshed refusals retain the confirmed snapshot. Migration extraction remains outside these catches:
a manifest with two sorted filenames sharing one timestamp still raises `MigrationOrderError`
before plan/mutation, closes the remote, and retains the accepted CLI exit-2 controller failure.
The closed host observer retains full schema and policy ownership; arbitrary Python type misuse
is not a new supported input model.

All existing consequential assertions remain. Workflow fixtures now supply genuine deployment and
migration DTOs, full observer-shaped authority and controlled source evidence without shared mutable
archive writes. Pyinfra/systemd fixtures explicitly authorize their intended create-only resources.
The packaged explicit-artifact consumer supplies its archive in the invocation; default packaged
provisioning still uses the real observer, target/downgrade policy and isolated helper entrypoint.
Coverage retains ordering, credentials, warning/cancellation outcomes, source/authority drift,
mutation aggregation, Caddy binding, partial installation, replay and scheduler ownership.
Ten additional cases cover missing evidence, empty preparation authority, malformed first/refreshed
projection evidence and the accepted duplicate-version exclusion; no general tests were removed.

**Delivered verification (2026-09-16).** Red/green proof demonstrated missing preflight/scheduler evidence previously
reached convergence; malformed scheduler, missing projection, protection record and independent
reference inputs previously escaped their intended safety results. Distinct scoped authority review
approved the final delta with no remaining important findings. Independent checks passed 113 focused
cases and all nine packaged provisioning scenarios, then freshly passed six refusal/migration cases
and isolated both prune branches plus refreshed warning/snapshot retention. Final full four-worker
operations gate passed 1,582 tests with 158 known fork/thread warnings in 62.08 seconds. Locked sync,
complete compileall, shell syntax, provision help, diff whitespace and scoped terminology checks
passed. `mix precommit` passed 805 tests in 41.7 seconds and changed no files. Source and test
delivery is committed as `64d68d1a838035fdbf12346018014f50a4078967`; documentation and issue state
were reconciled at the delivery checkpoint. Next: discuss candidate 5 before a separate bounded
design approval. Candidates 5–8 and pre-merge steps 4–6 remain ordered and gated.
No runtime saving, release build or native-host acceptance is established; history/external gates remain separate.

## 5. One owner of systemd asset bytes

**Evidence and boundaries.** [systemd.py](../../ops/taskman_ops/services/systemd.py)
`SystemdAsset` permits source path, text and binary content, while production constructors use
only text/bytes. `declare_systemd` and provisioning `_systemd_asset_sha256` each dispatch over
those representations. Pre-convergence authority and declaration separately build a systemd plan,
including scheduled package assembly and native calendar validation.

**Proposal and return.** Normalize installed assets to exact bytes and carry a single rendered
plan through confirmation/validation and declaration. Split this into smaller designs if threading
the plan enlarges scope. Removing the unused source representation alone is cheaper but leaves
repeated construction. No measured provisioning speed improvement is established.

**Safety and verification.** Preserve exact helper digest, bytes/modes, protected environment,
native calendar validation, unit reload and scheduler creation/refresh policy. `ManagedBackupAsset`
also describes template sources; do not indiscriminately merge types. Verify confirmed digests
against installed bytes in `services/test_systemd.py`, `services/test_backups.py`, `test_pyinfra.py`
and isolated scheduled execution. Cover custom roots/schedules and native systemd diagnostics,
then complete local gates and scoped review. Package changes retain required build/package gates.

**Original discussion proposal (2026-09-16; superseded by the decision below).** Fresh source/consumer inspection
confirms production `SystemdAsset` constructors supply rendered text or package bytes; backup
service/timer template sources remain meaningful in `ManagedBackupAsset`. Recommend a bounded
first increment making installed `SystemdAsset` content exact bytes, encoding rendered text once,
and using those bytes for both authority hashing and pyinfra upload. Preserve destinations, modes,
helper checksum verification, protected environment installation, native calendar validation,
reload conditions and scheduler create/refresh ordering. Verify digest/upload byte equality and
existing consequential assertions across systemd, backup, pyinfra and isolated scheduled consumers.
Full local gates and distinct scoped review remain required. Carrying one rendered plan through
confirmation and declaration is a separate, still-pending choice with larger workflow scope;
bytes normalization alone deliberately leaves repeated plan construction. Keeping the current
implementation is also available. No speed improvement or candidate 5 implementation is approved.

**Operator decision (2026-09-17).** Deliver candidate 5 in two ordered increments:

1. **Approved now:** normalize installed `SystemdAsset` content to exact bytes; encode rendered
   text once and use the byte representation for authority hashing and upload. Preserve all safety
   and verification requirements above. Repeated plan construction remains temporarily.
2. **Agreed follow-up, implementation not yet approved:** after the first increment is verified
   and committed, design carrying one rendered plan through confirmation, refreshed validation and
   declaration. Explicitly establish plan lifetime and refreshed-input handling so stale inputs
   cannot reach installation. Obtain separate bounded-design approval before implementation,
   then verify and commit that increment before continuing to candidates 6–8.

The full single-plan approach is the agreed destination, not an optional idea to drop after bytes
normalization. The staged approach accepts two verification checkpoints to keep each change easier
to assess. No speed benefit is established; external and history gates remain unchanged.

**Bytes-only delivery (2026-09-17; `tas-sr4b.24`).** Source/tests committed as
`bd8e044d5c15a617c553347069765c57ea081712`. `SystemdAsset` now owns validated bytes; rendered
unit text and the non-secret backup environment encode once at plan construction. Pyinfra uses
`BytesIO` for asset/environment uploads; pre-convergence hashing reads exact content directly.
The unused source/text/binary representation dispatch and `_systemd_asset_sha256` are removed.
`ManagedBackupAsset` template sources and separate plan construction remain unchanged.

Existing destinations, permission bits, helper checksum verification, reload conditions, enablement,
protected writes and partial scheduler create/refresh assertions remain. Extended systemd tests
verify byte assets, upload/plan equality and helper upload checksum. The actual pyinfra installation
test now verifies installed bytes as well as modes, including the backup environment. One new
controller-boundary test captures the authority request and checks all four resource digests against
rendered bytes. Custom roots/calendar diagnostics and isolated scheduled recovery retain coverage.

Red proof: two focused tests failed on text assets and `StringIO` uploads before the change.
Green: 48 focused systemd/backups/pyinfra tests and three isolated scheduled recovery cases passed.
Fresh full operations gate: 1,583 passed, 158 known fork/thread warnings, 60.44 seconds. Locked sync,
complete compileall, shell syntax, provision help, consumer searches, scoped terminology and diff
whitespace checks passed. Distinct read-only scoped correctness review inspected the exact delta
and consumers, found no issues and approved subject to the final Mix gate. `mix precommit` then
passed 805 tests in 43.3 seconds. Its initial failure was local PostgreSQL unavailability; restarting
the existing stopped container preserved data and restored the prerequisite.

No release-build or native-host acceptance occurred. These controller-only files are excluded
from both generated helper archive allowlists; packaging implementation did not change. The agreed
single-plan follow-up is still unfinished and requires its bounded design/approval before code.
No timing benefit, push, merge, history rewrite or external action is established.

**Single-plan design approval (2026-09-17; `tas-sr4b.25`).** The operator approved a
required immutable `SystemdPlan` in `ProvisioningInputs`, constructed once per material-plan
cycle before preflight and confirmation. Authority digests, refreshed authority checks and
systemd declaration consume that same plan without reconstruction. A pre-confirmation
clean-source retry discards the cycle and constructs a new plan; post-confirmation drift retains
the existing refusal and requires a new invocation. Configuration, secrets and rendered bytes
remain frozen through confirmation. For explicit-artifact or allowed-dirty runs, later local
asset edits require rerunning to apply them. Genesis retains its separate scheduler refresh
lifecycle; this is not a common byte snapshot across provisioning and genesis.

Accepted costs: required plan preparation in callers/fixtures, snapshot semantics for local
asset changes and reconstruction when a pre-confirmation cycle restarts. Benefits are exact-byte
consistency and clear ownership; no timing saving is established. Verification must prove
frozen-byte consistency/no reconstruction and cycle lifetime while retaining drift refusals,
checksum/mode/calendar/reload behavior and create-only scheduler authority. Full local gates,
distinct scoped review and local commit precede candidate 6. No implementation-plan document or
clean-session boundary is needed for this approved bounded increment.

**Single-plan delivery (2026-09-17; `tas-sr4b.25`).** Source/tests committed as
`ea4501b5cfa310b34b2296cfa4dfe54605c4f7b0`. Required, type-checked `SystemdPlan` is built at
one workflow location after successful target validation and clean-source stabilization. Both
preflight observations hash its exact bytes; `replace` retains the same plan when attaching
scheduler creation authority; declaration uploads it without reconstructing assets.

Assertion mapping: the authority digest test now hashes the supplied plan and refuses reconstruction;
the declaration upload/checksum test refuses reconstruction while retaining all upload, mode,
reload and enablement assertions. The actual pyinfra installation test compares installed bytes
and modes with the supplied plan. A new explicit-artifact workflow regression changes the source
renderer after the first preflight and proves refreshed authority/convergence retain the same plan
and original unit bytes. The clean-source retry regression proves discarded unstable candidates
render no assets and only the fresh stable target reaches the confirmed plan. All required input
fixtures were migrated; existing host/source/downgrade drift refusals and scheduler lifecycle
assertions were retained.

Red proof before implementation: two workflow cases failed on zero rendered plans and missing
`systemd_plan`. Green: 116 focused tests passed in 9.85 seconds. Fresh full local gates:
1,584 operations tests passed with 158 known fork/thread warnings in 59.90 seconds;
`mix precommit` passed 805 tests in 41.7 seconds. Locked sync, compileall, shell syntax,
provision help, consumer/terminology, changed-file whitespace and four changed-document local
link/anchor checks passed. Distinct read-only scoped correctness review found no issues and
independently reproduced four focused cases (0.67 seconds).

No timing saving is claimed. Changed controller modules are excluded from both generated helper
archive allowlists; no archive implementation changed, release build or native-host acceptance
occurred. Candidate 5 is complete; candidate 6 discussion is next, followed by 7–8 and the
unchanged test-overlap, history/rebuild and exact-head external acceptance gates.

## 6. Unused internal names

**Evidence and boundaries.** [config.py](../../ops/taskman_ops/config.py) has twelve properties
under “Compatibility aliases used by future host/service capabilities” without current consumers.
`secrets.py` similarly has unused signing-secret properties and `render_pgpass_file`/
`render_runtime_pgpass` aliases. These are internal Python names, not the model's YAML `AliasChoices`.

**Proposal, safety and verification.** Remove only names proved unused by a fresh repository and
generated-package consumer search. Retain derived paths and documented input aliases; pyinfra's
`ssh_hostname` inventory key is unrelated. Benefit is small surface reduction at low cost/risk,
so this follows changes with greater ownership benefit. Run config/secrets tests, packaging checks
where affected, and complete local gates. Do not add deprecation shims or broaden input changes.

**Operator decision (2026-09-17; `tas-sr4b.26`).** Approved removal of the twelve
`EnvironmentConfig` compatibility properties (`environment`, `ssh_hostname`, `administrator_user`,
`pinned_host_key_fingerprint`, `expected_public_ipv4`, `expected_public_ipv6`, `app_port`,
`erlang_distribution_port`, `database`, `database_user`, `target_architecture`, `os_release`),
`SecretConfig.ash_signing_secret`/`token_signing_secret`, and the `render_pgpass_file`/
`render_runtime_pgpass` assignments and exports. Preserve supported YAML `AliasChoices`, derived
paths, canonical signing-secret access, `render_pgpass`, redaction and secret-protection behavior.
Fresh consumer inspection precedes removal. Any real consumer requires escalation rather than
scope expansion. Benefit is modest navigation/maintenance clarity; no runtime saving is expected.
Existing contract coverage, full local gates and distinct scoped review precede commit/candidate 7.

**Delivery evidence (2026-09-17; `tas-sr4b.26`).** Source committed as
`be006188de5df044795961528f523d82127c5778`. The delta removes only the approved sixteen
Python aliases and their obsolete compatibility comment/exports. YAML input aliases, derived
paths, canonical fields/rendering and secret validation, registration/redaction and introspection
protections are unchanged. Fresh direct, reflective, import/re-export and generated-package
consumer inspection found no caller; unrelated `environment`/`database` attributes and pyinfra
inventory keys were distinguished from configuration properties.

Existing contract tests were retained unchanged rather than adding absence-only tests. Config/secrets
coverage passed before and after removal: 135 tests in 0.40 seconds each. Fresh full operations
suite: 1,584 passed, 158 known fork/thread warnings, 55.23 seconds. `mix precommit`: 805 passed,
44.0 seconds. Locked sync, compileall, shell syntax, provision help, consumer/terminology,
changed-file whitespace and three changed-document local links/anchors passed. Distinct scoped
review found no issues, independently reproduced all 135 focused tests (0.34 seconds), and built
fresh host/scheduled-backup zipapps confirming both exclude `config.py` and `secrets.py`.

Candidate 6 is complete; discuss candidate 7 next, then 8. No release build, native-host acceptance,
external action, push, merge or history rewrite occurred. Complete later coverage-overlap,
history/rebuild and exact-head external acceptance gates remain required.

## 7. Unused pristine admission

**Evidence and boundaries.** [acceptance.py](../../ops/taskman_ops/host/acceptance.py)
`validate_supported_host` has test consumers in `ops/tests/host/test_facts.py` and
`host/test_acceptance.py`, but no production caller. Provisioning uses
`validate_provisionable_host`; operations use their distinct operational/restore validators.

**Proposal and trade-off.** Map every pristine-only test scenario to the current production
admission entry point, then remove the obsolete path if coverage still exercises the same
consequential refusals. The production reduction is modest compared with test migration cost.
Retain evidence collection and command-specific capacity policy; do not delete tests by name alone.

**Verification.** Host facts/acceptance and public provision/preflight tests must retain foreign
listener/service, DNS/platform, absent-versus-unavailable, pristine/partial/managed and unsafe-path
outcomes. Complete local gates and scoped coverage review. This is production-path correction,
not authorization for the general duplicate-test removal scheduled in step 4.

**Operator decision (2026-09-17; `tas-sr4b.27`).** Approved mapping all old pristine-only
admission tests to production `validate_provisionable_host`, then removing `validate_supported_host`,
its export and its sole-consumer `_managed_conflicts` helper. Preserve consequential platform,
capacity, DNS, sudo/SSH-port, discovery-availability and foreign/contradictory/unverifiable
resource refusals. Current compatible partial/managed acceptance remains intended; blanket
refusal solely because a resource exists is obsolete policy rather than a production guarantee.
Keep operational and restore validators distinct. Every old consequential assertion needs an
explicit disposition; no general duplicate-test deletion is authorized. Full local gates and
independent scoped coverage review precede local commit and candidate 8 discussion.

**Assertion mapping (`tas-sr4b.27`).** All seventeen former call sites in
[host facts tests](../../ops/tests/host/test_facts.py) invoke production
`validate_provisionable_host` with the existing synthetic expected Caddy digest. Successful
fact assertions read `discovery.facts`. Existing parameter fixtures and consequential assertions
remain; pristine success additionally asserts `ProvisioningState.PRISTINE`.

| Current test | Preserved evidence |
| --- | --- |
| `test_provisionable_pristine_host_returns_normalized_immutable_facts` | Normalized facts, full snapshot consumption, frozen facts; adds pristine classification |
| `test_provisioning_refuses_contradictory_caddy_evidence` | Contradictory Caddy authority refuses with safety status |
| `test_unsupported_platform_is_refused_only_after_all_facts_are_collected` | Complete snapshot before unsupported-platform refusal |
| `test_unsupported_host_facts_map_to_status_two` | Platform/memory/disk-capacity failures retain status 2 |
| `test_privilege_or_active_connection_port_failures_map_to_status_five` | Privilege/active SSH-port failures retain status 5 |
| `test_failed_required_fact_command_maps_to_status_five_after_the_snapshot` | Required-command failure retains status 5 and complete collection |
| `test_inability_to_inspect_non_database_managed_state_refuses_preflight` | Unavailable managed resource evidence retains preflight refusal |
| `test_pristine_host_skips_postgresql_sudo_and_database_discovery` | Absent PostgreSQL skips privileged/database discovery |
| `test_absent_managed_units_pass_native_systemd_discovery` | Native systemd distinguishes absent units from unavailable discovery |
| `test_absent_postgresql_passes_shell_discovery` | Native shell discovery accepts absence without failed checks |
| `test_present_postgresql_requires_sudo_and_detects_a_managed_database` | Present database without required managed boundaries retains safety refusal |
| `test_partial_postgresql_installation_refuses_without_running_privileged_inspection` | Incomplete installation refuses preflight without sudo/psql inspection |
| `test_inability_to_inspect_present_postgresql_refuses_preflight` | PostgreSQL sudo/connection discovery failures retain preflight refusal |
| `test_preflight_requests_integer_memory_and_nearest_existing_ancestor_capacity` | Exact integer-memory and both configured-root capacity commands |
| `test_direct_public_dns_must_include_only_the_configured_vps_address` | Nonmatching direct DNS retains status 2 |
| `test_foreign_or_unverifiable_managed_listener_path_unit_or_account_is_a_safety_refusal` | Existing foreign/unverifiable resource fixtures retain safety refusal |
| `test_taskman_listener_requires_the_exact_managed_beam_process` | Managed listener ownership assertions retain a production-admitted facts baseline |

Only three test names change: `test_valid_supported_host_returns_only_normalized_immutable_facts`,
`test_pristine_only_validation_still_refuses_contradictory_caddy_evidence`, and
`test_existing_managed_listener_path_unit_account_or_database_is_a_safety_refusal` map to the
first, second and sixteenth rows respectively. All other names retain their mapping directly.
Existing production Caddy/PostgreSQL partial/managed acceptance and incompatible ownership tests
remain unchanged. The deleted `test_acceptance_boundary_exports_supported_host_validation`
asserted only that the obsolete function export was callable; it supplied no behavioral coverage.
This is the only test deletion, not general duplicate-test removal.

**Delivery evidence (2026-09-17; `tas-sr4b.27`).** Source/tests committed as
`b43fa667372b52a1e31be23be47355c7d7b354c6`. The seventeen mapped host-facts cases passed
against existing production admission before legacy removal: 61 host-facts/acceptance tests in
0.45 seconds and 65 provision/pyinfra tests in 7.16 seconds. After removing the unused validator,
helper/export and callable-only test, 125 host-facts/provision/pyinfra tests passed in 7.33 seconds.
The one-test reduction is exactly the obsolete export-shape assertion; no other test was deleted.

Fresh full operations gate: 1,583 passed, 158 known fork/thread warnings, 54.54 seconds;
`mix precommit`: 805 passed, 43.9 seconds. Locked sync, complete compileall, shell syntax,
provision help, removed-name/consumer and scoped terminology checks passed. Distinct scoped
coverage/correctness review found no issues, confirmed every old consequential assertion and
independently reproduced all 60 host-facts tests. It also checked that the expected Caddy digest
fixture matches `render-caddyfile` for the configured environment including the trailing newline.
Changed-document local links/anchors/whitespace and remaining sequence/gates were checked.

Only the unused production wrapper/helper/export were removed; operational/restore policy,
production partial/managed classification and native fact collection remain unchanged.
No packaging implementation changed; the modified controller module is outside the generated
host-helper archive allowlists. Final GitButler inspection retained a residual removal report
for the committed callable-only test; direct tree/worktree checks confirm the intended deletion
is already delivered. No workspace metadata rebuild occurred. Native/external acceptance remains unproven; no release build,
host action, push, merge or history rewrite occurred. Candidate 7 is complete; candidate 8
review/design is next before the unchanged later test-overlap/history/rebuild/external gates.


Checkpoint recovery evidence (2026-09-17): the operator authorized a checkpoint push, followed
by local GitButler state repair. Pushed head `24b0d47f` and origin matched and omitted the removed
test. Teardown exposed its `AD` stale-index signature plus older index entries across the branch.
All 506 tracked working-tree files matched the pushed tree byte for byte; no untracked path was
absent from that tree. Refreshing only the index to HEAD, then reinitializing GitButler, produced
no uncommitted changes and an applied tip matching origin. No file content or history changed.
Fresh `mix precommit` passed 805 tests in 42.6 seconds. Candidate 8 and all later gates remain pending.

## 8. Selected-handler imports

**Evidence.** The entrypoint eagerly imports all operation families; the isolated integration
harness imports and substitutes them unconditionally too. The
[startup measurements](2026-09-16-operations-test-parallelism.md#current-archive-startup-investigation)
show smaller discovery/preflight closures, while mutations retain most dependencies. Median
entrypoint import was 0.1838 seconds versus 0.1270 for discovery and 0.1020 for preflight; these
import-only measurements do not establish end-to-end savings. Earlier operator disposition
completed the investigation and deferred production import changes to this audit.

**Proposal and return.** After result/migration ownership settles, consider ordinary local
selected-handler imports only if the production boundary becomes clearer. Compare keeping the
current direct callable map with a simple explicit selected-handler approach. Avoid a generic
dispatcher, persistent bytecode lifecycle, reused process or harness framework for an unmeasured
test saving. Lower rank reflects uncertain return and test setup/dispatch cost.

**Verification.** Retain fresh children, actual archive entrypoint, `python -I -S`, all relevant
native-effect substitutions, readiness markers and timeout cleanup, fresh host authority and
every consumer assertion. Measure actual focused public cases before/after; import-only timings
are insufficient. Exercise failure observation as well as reads/mutations, packaging and complete
local gates. No startup change is approved.

**Operator disposition (2026-09-17).** Skip this candidate. Import-only savings do not establish
end-to-end benefit; obtaining that benefit would require changes to both production dispatch and
the integration harness, while mutations retain most dependencies. Retain the current direct
callable map and harness setup. No selective-import experiment or implementation is pending.
The earlier measured investigation remains evidence, not a startup acceptance claim.

## Retained complexity and rejected directions

- Keep full-history reference validation, its bounded projection and SQLite-backed incremental
  checks. Large modules alone do not justify another observer/recovery framework or cached authority.
- Keep restore OID/creation/replacement binding, migration protections/retirement and backup
  publication/deletion ordering. Similar retention syntax does not establish a shared operation policy.
- Keep bounded SSH channel setup/draining and separate argv-based native subprocess execution;
  moving `remote.py` into more files would not remove behavior or state.
- Keep fresh pinned SSH after firewall changes, Caddy candidate validation and PostgreSQL native
  HBA/parser/cluster authority. A generic guarded-service workflow would obscure different consequences.
- Leave the [PostgreSQL Python proposal](../specs/2026-09-09-postgresql-host-python-design.md)
  parked and the [CLI UX proposal](../specs/2026-09-09-operations-cli-ux-design.md) separate.
- Do not extract the application-version parser merely to shorten `build.py`, rewrite toolchain
  lookup, replace protected secret values, merge all JSON/digest helpers, or redesign packaging
  without a concrete net benefit. Repeated small helpers alone are insufficient evidence.
- Keep independent admission, transport correlation, privileged apply and final verification checks.
  They answer different questions at different consequence boundaries.

## Continuation and verification record

Current position: production review is complete. Candidates 1–7 are delivered and locally
committed; candidate 8 was explicitly skipped on 2026-09-17. Candidate 7's source/test checkpoint
is `b43fa667`.
Next: step 5 commit-grouping discussion; step 4 delivery `tas-sr4b.29` is complete. The
[completed overlap mapping and approved bounded removal](2026-09-17-operations-test-overlap.md)
own the exact three local duplicates and retained coverage. Full local tests, scoped review and pre-consolidation clean/dirty build/package gates
passed; the linked overlap review owns exact evidence. Accepted startup/archive/isolation/
native-effect constraints and earlier coverage trade-offs remain. No general test deletion is
approved. Step 4 completion precedes explicitly authorized commit grouping/squash and
identity-sensitive rebuild, followed by separately authorized exact-head VPS acceptance.
The handoff preserves the complete sequence and external gates.

Decision checkpoint verification (2026-09-17): register, handoff/index and live Beads state
agree on candidate 8 skipped and ordered review closed. Handoff diff retires only the skipped
import work and completed clean-session instruction; steps 4–6, coverage constraints and all
history/external gates remain. Changed Markdown paths/whitespace passed; `br sync --status`
reported in sync; `mix precommit` passed 805 tests in 43.8 seconds. No production/test code
changed, so the operations suite was not repeated and no behavior-parity claim is made.

Initial audit evidence (before source changes): live GitButler/task refresh, source/consumer inventory and direct boundary/test
inspection; the synthetic mutation-category probe above reproduced without host or native effects.
Four affected documents passed local link/anchor, whitespace and portable-markup checks; the handoff
diff retained every unfinished commitment and gate. `mix precommit` passed 805 tests in 43.3 seconds.
Only the register, documentation indexes/handoff and Beads export changed. The operations suite was
not repeated for this documentation-only audit; no proposed-change runtime parity or real-host
acceptance is claimed. Detailed checkpoint evidence is in `tas-sr4b.16`.

Candidate 1 delivery evidence: 496 distinct focused cases passed, including actual generated helper
and scheduled archive entrypoints/authority checks under `-I -S`. Fresh complete operations gate:
locked sync, compileall and shell syntax passed; four-worker pytest passed 1,492 tests with 158 known
gevent/PTY deprecation warnings in 59.06 seconds; `mix precommit` passed 805 tests in 43.0 seconds.
A distinct scoped reviewer inspected the implementation directly and approved it without blocking
findings; the earlier invalid-history refusal above is explicitly disclosed. Consumer and scoped
terminology searches passed. Documentation links, anchors, whitespace and handoff commitments were
checked. No native host acceptance, timing improvement, push, merge or history rewrite is established.
Candidates 1–2 and candidate 3 producer approval are satisfied; remaining increments retain their approval gates.

Candidate 2 delivery evidence: 94 focused secret/provision/output/pyinfra cases passed. Distinct
scoped secret-handling review approved without blocking findings, independently passing 35 secret
tests and five exception/coercion/sibling-cleanup checks. Fresh locked sync, complete compileall,
shell syntax, consumer and scoped terminology/whitespace checks passed. Complete four-worker pytest
passed 1,514 tests with 157 gevent/PTY deprecation warnings in 62.92 seconds; `mix precommit` passed
805 tests in 44.1 seconds. Documentation/reference and handoff-gate checks passed. No real SOPS,
native host acceptance or timing saving was established. Prior work was committed first as requested;
candidate 2 was subsequently committed as `d4335ffe` at operator request. No push, merge or history
rewrite was performed by the agent.

Candidate 3 producer delivery evidence: actual deploy/genesis `CommandError` results now emit
canonical `inspection`/5, preserving proved mutation, passing report, backup identity, warnings and
operation-owned final observations or truthful unavailable facts. The protocol narrowly accepts
deploy/genesis inspection/5 alongside existing 8; other operation/category restrictions remain.
Sixteen new regressions and 139 focused cases passed. Distinct scoped correctness review passed
without findings, independently running the 16 regressions and checking 224 category combinations.
Fresh locked sync, complete compileall, shell syntax, scoped whitespace/terminology and documentation
checks passed. Complete four-worker pytest passed 1,530 tests with 157 known deprecation warnings in
58.67 seconds; `mix precommit` passed 805 tests in 42.8 seconds. No translator or generic fallback
change occurred. The correction was committed as `664926a7`; no native host acceptance or timing saving is
established. This producer checkpoint preceded the separately approved translator removal, now delivered in
`tas-sr4b.22`. Candidate 4 was subsequently delivered in `tas-sr4b.23`; candidate 5 discussion is next,
followed by candidates 6–8 and the unchanged steps 4–6 gates.

Resume reconciliation evidence (2026-09-16): clean workspace at `2fb82749`; live issues confirm
candidate 4 closed, ordered review in progress and parent open. Stale register continuation and
parent description were reconciled without changing approval gates. Changed-document local links
and whitespace passed; handoff diff preserved every remaining step and gate; Beads export changed
only the parent description/timestamp and `br sync --status` reported in sync. `mix precommit`
passed 805 tests in 41.9 seconds. No production source changed and the operations suite was not
repeated for this documentation checkpoint. Candidate 5's bytes-only recommendation is unapproved.

Decision-record verification (2026-09-17): changed-document local links and whitespace passed;
reviewed handoff diff retains the complete pre-merge sequence and external gates verbatim. Live
`br show` confirms both owning issues retain the approved bytes-first increment and agreed gated
single-plan follow-up; `br sync --status` reports in sync. Beads export changes are limited to
those descriptions and timestamps. `mix precommit` passed 805 tests in 42.3 seconds. No production
implementation, operations-suite repetition or external action occurred in this documentation task.
