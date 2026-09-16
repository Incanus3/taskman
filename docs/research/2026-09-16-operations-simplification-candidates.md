# Operations simplification candidate register

Status: candidates 1–2 and candidate 3 producer correction complete; translator-removal design and later review pending. Updated: 2026-09-16.
Workstream: [Operations VPS readiness](../handoffs/ops-vps-readiness.md), pre-merge step 3.
Tracking: `tas-sr4b.16`; parent `tas-sr4b` remains open for hardening and external acceptance.

## Scope and authority

This register records architecture and simplification opportunities in production operations code
at `dedicated-host-deployment-automation` revision
`437cb1d217dfa39784436c3c3ebc93b269ff9160`. The checkout was clean before the audit.
It is an ordered discussion agenda, not an approved specification or implementation plan.
Candidate 1's bounded design was approved and implemented on 2026-09-16; `tas-sr4b.17` owns delivery evidence.
Candidate 2 was approved and delivered after a commit-first checkpoint (`15602d43`); `tas-sr4b.18` owns evidence.
Candidate 3's first producer correction is approved in `tas-sr4b.19`; translator removal remains unapproved.
No later candidate is authorized for implementation. Review candidates in the order below, record the
operator's disposition, and implement only an explicitly approved bounded design. Refresh consumers
and affected authority before implementation; reconsider ranking if an earlier change alters the evidence.

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
| 3 | Retire old-shape mutation translation with truthful exact failure handling | Remove a second result interpretation path; address demonstrated evidence degradation | Moderate / high, failure evidence | Producer correction completed (`tas-sr4b.19`); translator-removal policy pending |
| 4 | Make provisioning injection use the production evidence contract | Remove compatibility branches and implicit all-create authority | Moderate / high, resource/scheduler authority | Unreviewed |
| 5 | Give confirmed systemd asset bytes one owner | Fewer representations; bind validation and installation to the same rendered content | Moderate / moderate | Unreviewed |
| 6 | Remove unused internal compatibility names | Smaller supported internal surface | Small / low | Unreviewed |
| 7 | Retire unused pristine-only admission after mapping its tests | One production path per admission capability | Moderate / moderate; small production reduction | Unreviewed |
| 8 | Consider plain selected-handler imports | Smaller read-operation import closure, if production becomes clearer | Moderate / moderate; end-to-end benefit unproved | Unreviewed; revisit after earlier changes |

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
The [implementation plan](../plans/2026-09-16-exact-helper-failure.md) awaits approval in `tas-sr4b.21`. It includes correcting the additionally discovered service-stop producer to
service/8 before translator removal. No remaining implementation is approved.

**Next proposed increment (2026-09-16).** Split delivery so the demonstrated producer defect is
corrected before removing translation. First, host deploy's `CommandError` observation failure
must emit canonical `inspection` (accepted with exit 5), not unsupported `observation`. Preserve
the operation-owned mutation classification, final observation/availability, report, backup identity
and warning evidence. Verify the production failure branch and exact result through the entrypoint
without translation or a second observation, including a proved change and unavailable final facts.
Run scoped host/protocol/controller failure tests, complete local gates and distinct correctness review.
The operator approved this bounded producer correction on 2026-09-16; it is implemented and verified
locally in `tas-sr4b.19`. Translator removal remains a separate unapproved increment.

Implementation discovery: the general protocol category table admits `inspection`/5, but its
deploy/genesis override only admits 8. Producing the approved valid exact `inspection`/5 state
therefore also requires narrowly accepting 5 alongside existing 8 for deploy/genesis inspection.
Selection/service/history remain 8; restore inspection remains 11 and cleanup remains 10. This is
necessary for the approved result contract, not a broader category change. Scoped protocol tests
must protect those exclusions, alongside the actual handler/entrypoint regression.

Second, complete the failure-policy design and test mapping for removing `_legacy_mutation_result`.
Valid exact results must pass directly; malformed results must never become success or fabricated
unchanged state. Resolve which independently validated exact mutation/report/completion evidence
can be retained, preserve primary failure versus follow-up inspection failure, and preserve bounded
safe observation/lock behavior. Older injected result shapes must be replaced with exact fixtures
without losing consequential assertions. This second increment has its own approval gate; the
producer correction does not authorize removing translation. Retaining the translator permanently
leaves duplicate interpretation and demonstrated evidence loss; deleting it first is rejected.

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

Current position: candidates 1–2 are committed (`15602d43`, `d4335ffe`); candidate 3's approved
producer correction is committed as `664926a7`. Resolve translator-removal failure policy before its
separate approval. The remainder of candidate 3 and candidates 4–8 remain pending. After each explicit
disposition, update this register and its tracking issue; retain unreviewed entries in order.
Approved changes require their bounded design, meaningful tests, complete local gates and distinct
scoped verification. Production stability precedes step-4 test overlap removal, then authorized
commit grouping/squash and identity-sensitive rebuild, then separately authorized exact-head VPS
acceptance. The handoff preserves the complete sequence and external gates.

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
established. Translator-removal policy and its separate approval remain next, followed by candidates
4–8 and the unchanged steps 4–6 gates.
