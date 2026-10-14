# Operations test overlap review

Status: completed coverage-decision record. Updated: 2026-10-13.
Owners: `tas-sr4b.28`, `tas-sr4b.29`, `tas-561x.4` (closed).

## Scope and authority

This record retains the exact three approved duplicate-removal mappings, rejected removals,
post-acceptance assertion ownership and the production-admission coverage migration.
The initial review inspected clean source `6153969bd7c8b153fe1e60a5093d9b8ac79d6538`;
the repeated review inspected thirteen changed operations test files in
`f1052417e651..b11d24f828f7` and their relevant production boundaries. Historical inventories
and results below describe those checkpoints, not today's collection or exhaustive defect coverage.
No further removal or implementation is pending from these reviews.

The [reconciliation specification](../specs/2026-09-18-operations-contracts.md#acceptance-scenarios)
owns acceptance and coverage requirements; the
[deployment design](../specs/2026-09-09-dedicated-host-deployment-design.md#test-design)
and [development guide](../guides/development.md#operations-verification) own current engineering
practice and checks. The
[accepted history split](2026-09-16-operations-test-parallelism.md#accepted-history-coverage-split)
retains its explicit combined large-history packaged-consumer trade-off.
Identified build receipts are retained privately; the
[native acceptance guide](../guides/native-acceptance.md) defines public verification criteria.

## Removal criterion and method

The historical reviews below removed tests exercising the same production boundary with
equivalent inputs, setup and consequential assertions, or strictly implied by a retained owner.
Matching names, assertion text or outcomes across different boundaries are insufficient.
Parser, controller, protocol, handler, entrypoint, native adapter and actual packaged-consumer
checks retain separate ownership. This does not require each layer to repeat the same input
matrix: current [test-layering guidance](../guides/development.md#prefer-simplicity-within-the-supported-reliability-boundary)
owns that distinction. No test-count or timing target justifies extra removal.

AST comparisons and inventory found leads; direct payload/fixture/call inspection proved the
three cases below. Assessments compared local, helper/protocol and controller/packaged coverage.
This was a bounded review, not exhaustive duplication certification. No runtime saving is claimed.

## Delivered duplicate removals

Exactly three duplicates were removed. Historical parameter suffixes identify removed cases;
remaining suffixes renumbered, so current verification uses payloads.

| Delivered removal (historical node ID) | Retained owner | Complete assertion and scenario mapping |
| --- | --- | --- |
| `ops/tests/test_config.py::test_install_root_topology_rejects_overlap_and_reserved_path_collisions[overrides0]` | `ops/tests/test_config.py::test_two_root_paths_must_be_absolute_normalized_disjoint_and_reserved_free[backup_root-/opt/taskman]` | Both build the fresh default environment with `backup_root=/opt/taskman`, retain `install_root=/opt/taskman`, invoke `EnvironmentConfig.model_validate`, and require `ValidationError` without inspecting a message. No mutation, transport or lifecycle effect is exercised. |
| `ops/tests/test_config.py::test_install_root_topology_rejects_overlap_and_reserved_path_collisions[overrides1]` | `ops/tests/test_config.py::test_two_root_paths_must_be_absolute_normalized_disjoint_and_reserved_free[backup_root-/opt/taskman/backups]` | Same fresh baseline and production call; both change only `backup_root` to a child of the installation and require `ValidationError`. No additional assertion or effect. |
| `ops/tests/test_cli.py::test_each_stable_exit_status_has_the_documented_numeric_code` | `ops/tests/test_simplification_contract.py::test_every_public_exit_category_is_retained` | Both iterate the same imported `ExitStatus` enum and assert the identical twelve name/value pairs: OK=0, INVALID=2, LOCAL_PREREQUISITE=3, SECRET=4, REMOTE_PREFLIGHT=5, BACKUP=6, MIGRATION=7, RELEASE=8, READINESS=9, SAFETY=10, RESTORE=11, LOCKED=12. The retained exact tuple additionally constrains iteration order; it implies the removed dictionary assertion. Neither calls CLI dispatch or checks error translation. |

Both environment helpers build the same fresh dictionary and apply the same overrides; no fixture
changes the path-validation behavior. The CLI secret-registry fixture does not affect enum iteration.
Only two topology parameter dictionaries and the weaker enum test were deleted; production,
test support, all other CLI/configuration cases and public packaged assertions were unchanged.
The four retained topology inputs are backup `/opt/taskman/current`, backup
`/opt/taskman/releases/backups`, installation `/var/lib/taskman` and backup `/var/lock/taskman/backups`.
Enum aliases are outside both original tests' coverage; removal neither adds nor loses alias detection.
No supported input, assertion, transition or execution boundary was lost for these three duplicates.

## Similar coverage retained

The initial review retained differing release-ID parameter sets and archive-member safety/layout
cases, parser consent states, service-port versus path invariants, foreign/malformed/ambiguous
listener evidence and distinct package consumers. Fresh package construction differs from
immutable-copy materialization; transient and scheduled executable allowlists differ.
Native firewall behavior differs from adapter change reporting; protected-write receipts,
idempotent writes, suppressed diagnostics and possible-mutation reporting differ.
Dry-run parsing differs from dispatch and real no-mutation workflows. Codec budgets cannot
establish stored-record, archive-admission or full-history correctness.

## Helper and protocol comparison

Keep both
`ops/tests/host_helper/test_entrypoint.py::test_mismatched_mutation_result_with_invalid_observer_emits_valid_unavailable_facts`
and `::test_wrong_type_mutation_result_with_invalid_observer_emits_valid_unavailable_facts`.
Both demand one observation, valid decoded unknown mutation facts, no report and a fully unavailable
final group. The inputs are different: a `HostResult` with foreign correlation versus an arbitrary
`object()`. `validate_result_for_request` rejects these at separate identity and type guards.
The shared fallback does not make the scenarios equivalent, and protocol unit checks do not
replace their entrypoint result/encoding behavior. Removing either would be an additional
invalid-input coverage trade-off, not a proved duplicate. No helper/protocol removal is proposed.

Retain the exact-result history/report, observer-reuse and production post-history observation
owners separately; protocol schema admission cannot establish producer truthfulness or no-relock
behavior. Keep encoding maximum cases for deploy, cleanup and restore: escaped reports, completion
lists and identity maxima constrain different payloads. Cleanup protocol completion validation
remains distinct from real deletion/reference-protection tests. The two real >4,096 history
observer regressions own valid full scanning/old references and malformed later-authority refusal,
respectively. No backup, protection, restore-target or record publication test was proved redundant.

## Controller and packaged-consumer comparison

At the original checkpoint, read-only discovery compared workflow tests with 29 end-to-end
test function definitions (48 collected cases); these are historical inventory counts. No same-boundary duplicate was proved in that comparison. Retain:

- `test_public_cleanup_executes_controller_plan_through_packaged_helper`,
  `test_public_cleanup_recovery_matrix_uses_real_filesystem_only_preflight`, and
  `test_public_cleanup_partial_batch_keeps_proved_completion` in `test_end_to_end.py`:
  real archive entrypoint/codec, filesystem deletion and native partial consequence. They do
  not replace `test_cleanup_collects_all_pages_confirms_once_and_executes_bounded_batches`
  in `workflows/test_cleanup.py`, which constrains inspect/execute requests, 64+1 batches,
  empty inspection authority, fresh execution facts, one confirmation and warning aggregation.
  `test_later_batch_loss_preserves_first_batch_completion_without_reusing_observations`
  separately owns controller projection after a constructed second-reply loss.
- `test_public_packaged_restore_converges_each_durable_database_family` in `test_end_to_end.py`
  owns ordinary same-backup recovery; `test_public_packaged_replacement_converges_each_unfinished_arrangement`
  in `workflows/test_restore_replacement.py` owns a different backup with `replace_unfinished=True`,
  previous/replacement plan identities, replacement dump loading and required safety-copy choice.
  Shared harness use does not make the operations equivalent.
- `test_public_packaged_restore_lost_reply_reports_unknown_after_real_consequence` in
  `test_end_to_end.py` executes a real packaged mutation; the constructed reply-loss test
  `test_unknown_dispatched_restore_reply_preserves_starting_state_and_unknown_observations`
  in `workflows/test_restore_recovery.py` isolates changed/unknown projection, starting backup
  authority and unavailable restore-database evidence. Both remain.

## Post-acceptance assertion ownership mapping

The repeated review retained every mapped owner; no additional same-boundary duplicate was proved
or removed. Assessments inspected changed test/fixture ASTs, actual boundaries and retained assertions.
Changed transport stubs enabled existing scenarios rather than replacing their coverage.
Paths below are relative to `ops/tests/`; this is a historical bounded mapping checked against
current owners, not a complete inventory of subsequent acceptance additions.

| Retained owner | Consequential assertions and boundary | Why apparent overlap remains |
| --- | --- | --- |
| `host/test_caddy_config_probe.py`: package-recorded systemd alias acceptance and ownership/checksum/supported-path guards | Executes shipped Caddy shell probe with native substitutions. Both package aliases admit; foreign owner, checksum drift, resolved mismatch and unrelated path refuse. | Host-facts admission consumes emitted evidence; it does not execute this probe. |
| `host/test_facts.py`: shared-owner omission, malformed-owner refusal, duplicate-after-omission refusal, shared SSH admission, resource Caddy refusal, exact managed BEAM ownership | Pure parser preserves unique Caddy owners while omitting valid shared owners; syntax and duplicate ambiguity refuse. Full admission then accepts unrelated shared SSH but refuses shared Caddy and missing application ownership. | Parser syntax, provisionable-host policy and managed application process identity are different boundaries. Valid multi-owner Caddy differs from malformed output. |
| `host_helper/test_discovery.py`: missing timer planned delta | LoadState=not-found is authoritative timer absence; empty UnitFileState/inactive ActiveState do not require a loaded unit. This produces explicit first-install scheduler facts. | Timer enablement and controller create authority are separate boundaries; absence must use real systemd semantics. |
| `host_helper/test_discovery.py`: broad-track selector, SQL input, failed identity SQL and failed cluster listing | Executes production PostgreSQL discovery shell with fake native commands. None-track probe uses nonempty broad selector; pinned-track SQL expands role/database variables through file stdin; each native failure raises instead of becoming absence. Existing `_postgres_authority_exit` matrix still exercises cluster/listener/process/role/database refusals with migrated stdin stub. | None versus pinned track are different inputs. Listing failure versus identity query failure are different native failure points. The old script-text-only test was replaced by execution coverage, not an additional removable duplicate. |
| `host_helper/test_operation_capabilities.py`: opt-in native pristine catalog and native restore SQL parser | Native pristine SQL accepts empty template then refuses a transaction-local public function and rolls back. Production preflight/catalog wrappers expand quoted variables; malformed SQL refuses. | Static catalog assertions and mocked argv/input checks cannot prove native PostgreSQL parsing. These opt-in owners remain despite default skips. |
| `host_helper/test_preflight.py::test_admin_query_feeds_quoted_variables_to_psql_file_input` | Preflight output, literal tab separator, ON_ERROR_STOP, role variable, no command SQL and exact newline bytes; retains the prior separator assertion. Existing capacity case owns filesystem and exact role-size parsing. | Catalog wrapper uses another callable/variable contract; native parsing alone does not prove production file/stdin wiring. |
| `host_helper/test_restore.py::test_connection_termination_feeds_exact_database_names_to_psql_file_input` | Exact canonical/temporary names, pg_terminate_backend SQL, self-PID exclusion and newline bytes through file stdin. | Read-only SQL wrapper tests cannot imply this mutation's exact target set or query. |
| `host_helper/test_restore_database.py`: quoted admin variables; absent/missing/empty/populated migration observations | Catalog actions get file/stdin variables; canonical uses TCP application pgpass while temporary uses local peer admin without environment override. Present-empty table remains distinct from missing table; state/OID/owner assertions remain. | Authentication paths and catalog/table/version states differ. No canonical/derived query-path merging or credential-target widening. |
| `host_helper/test_restore_database.py`: temporary pristine admin template; registered dump load; opt-in native FD0 load | Empty proof passes exact temporary mapping to admin pristine observer, without a usable pgpass. Pending registration refuses without launching; registered load has exact fixed shell/runuser/pg_restore/socket/role argv. Native dump enforces application current_user/postgres session_user, application object ownership, restored row and unchanged 0600 dump mode, with UUID-only cleanup. | Mock argv proves packaging/bridge contract; native SQL/CHECK/ownership/FD0 behavior is separate. The VPS acceptance report separately proves actual root-owned host descriptor behavior. |
| `host_helper/test_restore_database.py`: rebuild-before-drop, exact OID rename, replacement canonical drop | Existing registration/pending/OID/destination/original-preservation guards retain their prior refusal/no-command assertions; successful exact DROP/ALTER SQL now asserted in protected stdin. | The same SQL transport does not merge durable intent, rename destination safety and retired-original preservation. |
| `host_helper/test_state.py`: missing root mode and preserved existing root | Real filesystem/lock: creation leaves 0755 root and 0600 lock; pre-existing 0750 stays unchanged. Existing serialization/timeout owner remains. Fixture chmod establishes exactly 0750 under either umask. | Creation, preservation and contention have distinct starting state and consequences. No mirrored umask test added. |
| `host_helper/test_verification.py`: malformed/public/epmd/absent-database refusal; delayed listeners; exhausted budget; unsafe transition | Full verify result/check/exit evidence, immediate no-sleep refusal on unsafe topology, shared listener/local/public readiness budget, no HTTP probe after exhaustion/unsafe transition. Scoped-loopback/public-address parser callers remain. Existing host-preflight/result checks only gained keyword-compatible stubs. | Tri-state startup transitions differ from static topology parsing and HTTP readiness. None of the scenarios has equivalent inputs/assertions. |
| `host_helper/test_verification.py`: normal 11,907-byte journal; exact 64 KiB stdout/stderr; overflow both streams; late failure; timeout/nonzero/empty; default 9 KiB retained | Real bounded subprocess adapter under journal fixture: healthy capture has passed summary; exact threshold succeeds per stream; one byte over refuses with empty output; failure beyond old cutoff is detected; each unavailable/empty result is fixed and safe; other commands retain default limit. Existing startup-failure result owns full verify report. | Runner threshold admission, journal classification, caller-specific budget and whole verify projection are separate. Smaller generic `test_commands.py` limit checks do not exercise this caller's default/override. |
| `services/test_postgresql.py`: parent-PID before mutation matrix and existing native fixtures | Rendered shell succeeds only with matching postmaster parent PID; mismatch, blank, nonnumeric and SQL failure stop before config/restart. Existing socket/parser/inspection/convergence fixtures migrate from startup timestamp to parent-PID evidence. | Existing contradictory data-directory case tests a different identity field. Distinct inspection/pre-/post-mutation placements and original endpoint/restart/parser assertions stay. |
| `services/test_systemd.py`: canonical private state home | Rendered User/Group/StateDirectory/state/runtime mode agree with baseline-owned private directory. | Renderer-only and discovered-host checks do not prove this cross-owner relationship. |
| `workflows/test_provision.py`: allowed free-space refresh, non-capacity drift refusal, refreshed low-capacity refusal | Public confirmation workflow binds shown authority, accepts changes in both capacity counters, refuses managed-path drift, and uses real host admission to reject each independently low refreshed counter before convergence. | Existing generic drift and host-facts threshold cases do not prove this refresh/admission interaction. |
| `test_end_to_end.py`: packaged restore preflight command shim | Actual isolated archive consumer preserves existing role/capacity/name assertions; shim now requires byte stdin, strict UTF-8/newline, file-input/no-command and ON_ERROR_STOP for file-mode SQL. | Support migration enables existing packaged workflow scenarios; it is not a duplicate replacement for direct/native SQL contracts. Fresh children, actual entrypoint and all previous consumer assertions remain. |

The opt-in native checks were not rerun for that mapping, so mocked assertions do not claim native
acceptance. Identified native results and fresh provisioning belong to the acceptance report.

## Production admission coverage migration

Coverage mapping from 2026-09-17, checked against test names and
production admission on 2026-09-18. Later acceptance
adds coverage; this table records the migrated cases, not the full current test inventory.

**Assertion mapping.** All seventeen mapped call sites in
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

Existing production Caddy/PostgreSQL partial/managed acceptance and incompatible ownership tests
remain unchanged. The deleted `test_acceptance_boundary_exports_supported_host_validation`
asserted only that the obsolete function export was callable; it supplied no behavioral coverage.
This was the only deletion in that admission migration; it is separate from the three approved duplicate removals above.

## Test layering and retained coverage

The shared-host branch review found repeated integration matrices as well as literal duplicates.
Detailed combinations belong to the lowest owner of the behavior. Higher-level tests retain
representative wiring and distinct failure consequences. These reductions do not change runtime
behavior or remove the native acceptance requirement.

| Reduced coverage | Retained owner and consequence |
| --- | --- |
| Three identically stubbed SCRAM access failures | One `host_helper/test_preflight.py::test_scram_native_access_failure_never_uses_diagnostics_or_falls_back`; native authentication scenarios remain in `services/test_postgresql_native.py`. The removed parameter was unused. |
| PostgreSQL password-policy substring and duplicate late-access/unsupported-client tests | `services/test_postgresql.py::test_role_password_input_is_sensitive_stdin_not_a_password_argument` asserts exact SQL bytes. `test_late_access_failure_retains_created_resources` retains both native and transport failure variants plus merged resource/classification/redaction assertions; `test_unsupported_native_client_refuses_before_pgpass_and_resource_creation` retains the named prerequisite failure. |
| Firewall post-install policy details | `host/test_firewall.py::test_absent_package_binds_actual_compatible_saved_policy_and_preserves_rules` retains mutated and active-complete integration cases, digest binding, rule preservation and fresh-SSH behavior. `host_helper/test_firewall.py` owns source exceptions, existing coverage and family-specific additions. |
| Retained-credential Cartesian workflow matrix | `workflows/test_retained_credential_admission.py` keeps four valid override/dry-run pairs and byte-mismatch/unsafe-mode refusal representatives, including refusal before native use. `host_helper/test_preflight.py::test_absent_database_retains_exact_pgpass_file_authority` owns detailed retained-file states across authentication modes. |
| Host-key selection matrix through both SSH wrappers | `test_remote_host_keys.py` keeps every key/port/marker state through the shared verifier's pyinfra caller; interactive SSH keeps ordinary/hashed success and missing/revoked refusal representatives. Both wrappers retain their own exception and temporary-trust cleanup tests. |
| Caddy conditional activation and ready-public/missing-import end-to-end repetitions | `test_end_to_end.py` retains new-site activation, pending-to-completion-to-replay, and inactive/masked operator-activation paths. Helper tests own validated no-op and service/import classification; `workflows/test_provision.py::test_application_ready_proxy_pending_preserves_real_facts` keeps the ready-public/missing-import refusal and truthful pending facts. |
| Workflow pending-state and preparation-status matrices | One missing-import pending case and one readiness-failure case retain controller result/receipt projection and prevent finalization. `host_helper/test_caddy.py` and `host_helper/test_deploy.py` own concrete service and preparation failure distinctions. |
| Sensitive database/runtime failure crossed with site change/no-change | `test_pyinfra.py::test_public_provision_retains_native_receipt_after_real_sensitive_failure` keeps database failure after a changed site and runtime failure after an unchanged site. These retain both failure boundaries and prior-change classifications without repeating their Cartesian product. |

The retained cases preserve byte/mode authority, secret suppression, no mutation before refusal,
truthful mutation/cleanup receipts, conditional reload, finalization ordering and separate native
acceptance. Production source and native fixtures are unchanged. This is a bounded audit of
branch-added overlap, not a claim that every remaining test is necessary or that all duplication
has been eliminated.

Verification after the reductions: focused changed/retained-owner checks passed, then the full
operations suite passed with 2,569 tests and 24 skips; `mix precommit` passed with 1,210 tests and
32 exclusions. The operations gate retains its documented fork/thread warnings. Opt-in native
acceptance was not rerun; local gates do not establish actual host behavior or runtime savings.

## Complete branch test-additions audit

Scope: all 65 changed test, support and fixture files against `origin/main`, inspected at
`e4b0df61` before these reductions. This includes the complete branch deltas and relevant current
behavior, rather than only upper-level matrices. Production behavior is unchanged. The table
below accounts for every audited file; retained volume has an identified behavior or fixture
consumer, not automatic justification from its test layer.

Whole-test removals preserve meaningful assertions in stronger retained owners. The shared
PostgreSQL recovery reducer owns its full four-state matrix directly; entrypoint/controller tests
retain representative consequence handling. Shared verification loops retain representative wiring,
while distinct full/advisory outcomes, both timing envelopes and each inventory-reader deadline
remain covered. Historical Caddy vendor files and provenance metadata are retired: installation
fixtures use synthetic default-file contents, while generated native archives own package identity
and extraction checks. The dated archive-hash test had no independent behavioral contract.
Opt-in UFW checks retain real reader and warning behavior against operator-supplied trusted source,
without requiring one historical archive name or hash. Native Caddy fault hooks match the actual
candidate version and exact install options rather than a historical version.

The direct protocol test `test_configuration_override_refuses_the_preserved_configuration_branch`
owns refusal of an existing-configuration observation when configuration override was requested.

### Caddy

| Audited path | Disposition and coverage owner |
| --- | --- |
| `ops/tests/fixtures/caddy/2.11.7/Caddyfile` | Replace with `ops/tests/fixtures/caddy/package-default.Caddyfile`: synthetic contents used by installation fixtures. |
| `ops/tests/fixtures/caddy/2.11.7/conffiles` | Remove: unused vendor control file; no consumer or current per-release contract. |
| `ops/tests/fixtures/caddy/2.11.7/control` | Remove: unused vendor control file; native metadata test generates its own archive. |
| `ops/tests/fixtures/caddy/2.11.7/md5sums` | Remove: unused vendor checksum file; generated archives own extraction checks. |
| `ops/tests/fixtures/caddy/2.11.7/postinst` | Remove: unused maintainer script; authenticated package behavior is accepted without script allowlists. |
| `ops/tests/fixtures/caddy/2.11.7/postrm` | Remove: unused maintainer script; no native or fixture consumer. |
| `ops/tests/fixtures/caddy/2.11.7/prerm` | Remove: unused maintainer script; no native or fixture consumer. |
| `ops/tests/fixtures/caddy/2.11.7/provenance.json` | Remove: historical package metadata has no current fixture or behavioral consumer. |
| `ops/tests/host/test_caddy_config_probe.py` | Reduce: active/inactive classification representatives; literal imports and malformed authority remain helper-owned. |
| `ops/tests/host_helper/test_caddy.py` | Merge: root:caddy mode assertion into unchanged-authority test; keep parser, trust, identity and output bounds. |
| `ops/tests/host_helper/test_caddy_mutations.py` | Merge/reduce: `test_isolated_packaged_prepare_runs_filesystem_observer_and_mutation_path` owns the isolated prepared-entry assertions, with two fake package branches and one native archive; retain mutation/recovery/install failures. |
| `ops/tests/host_protocol/test_caddy.py` | Reuse shared request fixture; remove combined malformed guard tuple implied by individual-field cases. |
| `ops/tests/services/native_caddy_acceptance.py` | Keep: opt-in native fault/checkpoint harness owns correlated receipts, locks, physical closure and digests; install hooks use the actual candidate version. |
| `ops/tests/services/test_caddy.py` | Simplify: pure frozen-authority tests need no filesystem fixture; retain actual observations and dispatch-loss consequences. |
| `ops/tests/services/test_native_caddy_acceptance.py` | Merge/reduce: checksum/controller assertions, checkpoint and identity representatives; retain atomic private channel and native failure boundaries. |
| `ops/tests/support/caddy.py` | Keep: reused filesystem/native-command fixtures plus frozen protocol evidence; distinct consumers share these seams. |
| `test/taskman/caddy_config_test.exs` | Keep: custom-port shell renderer; existing test owns default port and normalization. |

### PostgreSQL and UFW

| Audited path | Disposition and coverage owner |
| --- | --- |
| `ops/tests/host/test_firewall.py` | Keep: controller invocation, confirmation drift, mutation/reply-loss evidence, cleanup and fresh SSH. |
| `ops/tests/host_helper/test_firewall.py` | Keep: ordered policy, per-family fallback, source exceptions, preservation, partial native mutation and execution bounds. |
| `ops/tests/host_helper/test_postgresql_listeners.py` | Keep: selected-port bounded native socket observation and identity refusal. |
| `ops/tests/host_helper/test_postgresql_recovery.py` | Keep: protected descriptor capture, metadata/drift, no-clobber, partial capture and retained recovery evidence. |
| `ops/tests/host_helper/test_preflight.py` | Merge/reduce: credential states once for absent database; ready deferral remains separate. Real proof owns SCRAM argv; default synthetic native client owns unsupported version. |
| `ops/tests/host_helper/test_ufw_adapter.py` | Merge: package loading, bytecode and namespace/hook restoration; controlled owner/mode matrix replaces ambiguous refusal. |
| `ops/tests/host_protocol/test_firewall.py` | Reduce: fixed-command authority owner covers two repeated malformed results; retain closed and correlated protocol evidence. |
| `ops/tests/host_protocol/test_postgresql.py` | Keep/add direct owner: selected cluster/resource/override guards and four recovery-reducer states. Opposite force/branch mismatch is tested directly. |
| `ops/tests/services/test_postgresql.py` | Merge/reduce: remove dataclass/operation-name-only tests; real declarations own staged bytes, permissions, native calls and package exclusion. |
| `ops/tests/services/test_postgresql_native.py` | Keep: sole real libpq/HBA/verifier owner; session SCRAM, adopted-password preservation and sibling health require native fixture. |
| `ops/tests/workflows/test_retained_credential_admission.py` | Keep: six previously reduced credential-admission representatives own early refusal, ordering, cleanup and redaction. |

### Deployment lifecycle

| Audited path | Disposition and coverage owner |
| --- | --- |
| `ops/tests/host_helper/test_backup.py` | Merge: exact SCRAM dump/size-query assertions into existing real backup secret-boundary test. |
| `ops/tests/host_helper/test_deploy.py` | Reduce repeated malformed-key loop to upload; installed equivalence remains. Keep preparation, protection, replay, locking and actual verifier consequences. |
| `ops/tests/host_helper/test_discovery.py` | Reduce repeated legacy selection and schema matrices; keep unique executable/owner guards and native/parser/resource owners. |
| `ops/tests/host_helper/test_entrypoint.py` | Reduce caller recovery matrix to representative capture; protocol owns four states. Isolated encoding and direct invalid-facts owners remain. |
| `ops/tests/host_helper/test_restore_database.py` | Merge: SCRAM no-password assertion into canonical-versus-peer observation test with exact query/env checks. |
| `ops/tests/host_helper/test_state.py` | Keep: all six inventory readers must propagate deadlines; snapshot and checksum expiry have separate consequences. |
| `ops/tests/host_helper/test_verification.py` | Merge/reduce shared successful/timing/lock setup; retain differing full/advisory outcomes, both envelopes, polling and inventory failures. |
| `ops/tests/host_protocol/__init__.py` | Keep: Python package marker. |
| `ops/tests/host_protocol/test_mutation_results.py` | Reduce duplicate escaped-envelope and failed-check matrices; real entrypoint encoding and native helper paths own these assertions. |
| `ops/tests/host_protocol/test_operations.py` | Keep: closed operation registry is a protocol contract. |
| `ops/tests/host_protocol/test_protocol_v3_budgets.py` | Reduce to maximum preparation budget for both target kinds; retain exact-path, oversized, misplaced and aggregate refusals. |
| `ops/tests/test_end_to_end.py` | Reduce final failure to TLS and lost reply; direct helper/entrypoint own malformed identity. Keep real packaged sequencing and cleanup. |
| `ops/tests/test_pyinfra.py` | Keep: real pyinfra context, warnings, prior-change classifications, sensitive receipts and stopped later mutation. |
| `ops/tests/workflows/test_deploy.py` | Keep: completion authority, per-field drift, empty-database backup admission and caller-specific failure projections. |
| `ops/tests/workflows/test_deploy_transaction.py` | Keep: both request-operation variants own wiring and exclude consent-derived host authority. |
| `ops/tests/workflows/test_discovery_request_v3.py` | Reduce default/override repetition: each consumer owns true-flag propagation; builder owns false default and validation. |
| `ops/tests/workflows/test_helper.py` | Reduce recovery caller matrix; retain transport dispatch/cleanup/forged evidence and direct target drift. |
| `ops/tests/workflows/test_provision.py` | Remove weaker four-case real-admission duplicate; stronger frozen-authority dry-run/override representatives retain immutability, redaction and convergence. |

### Transport, packaging and general support

| Audited path | Disposition and coverage owner |
| --- | --- |
| `ops/tests/host/test_baseline.py` | Keep: baseline package isolation and updated firewall keyword wiring; actual pyinfra modes remain covered. |
| `ops/tests/host/test_facts.py` | Reduce socket/admission matrices to wiring representatives; direct owner/parser/protocol tests retain detailed refusal states. |
| `ops/tests/host/test_packages.py` | Reduce installed-package branch repetition; retain required refresh/install ordering, signed-source handling and early native failure. |
| `ops/tests/host_helper/test_package.py` | Remove three isolated invalid-input repetitions; keep deterministic packaging, dependency policy, private credential entry and real isolated firewall consequences. |
| `ops/tests/services/test_systemd.py` | Keep: shared Caddy fixture adaptation reduces setup; no extra systemd scenarios added. |
| `ops/tests/support/architecture.py` | Keep: scanner changes enforce the one scoped OS-UFW dependency exception and remove obsolete shell-owner entries. |
| `ops/tests/support/listeners.py` | Keep: shared executable fixture proves selected ss query avoids oversized unrelated socket ownership output. |
| `ops/tests/support/ssh_keys.py` | Keep: shared disposable native OpenSSH key/store helpers replace fake key scanning. |
| `ops/tests/test_architecture.py` | Keep: these test the architecture scanner behavior, including exact allowed owner and import forms. |
| `ops/tests/test_checksums.py` | Keep: deadline before open/during read/EOF, stream closure and unchanged checksum are distinct low-level boundaries. |
| `ops/tests/test_cli.py` | Keep: pending13 public output, explicit override parser, command exclusion and blast-radius help are public contracts. |
| `ops/tests/test_config.py` | Reduce ordinary-port repetition; retain default configuration and explicit standard port plus fixed-EPMD conflict. |
| `ops/tests/test_executable_scripts.py` | Keep: bounded executable-string scanner owner changes with the retired firewall shell implementation. |
| `ops/tests/test_helper_runner.py` | Reduce generic interpreter/status rows; retain actual killed-helper passfile cleanup, timeout uncertainty and sensitive framing. |
| `ops/tests/test_integration_packages.py` | Keep: isolated package materialization/import and byte-warning containment; official UFW warning test remains opt-in. |
| `ops/tests/test_remote.py` | Reduce omitted/explicit-None duplicate; retain falsey context binding, explicit state and default, real transport bounds and warning evidence. |
| `ops/tests/test_remote_host_keys.py` | Keep: earlier-reduced native key-selection matrix and distinct wrapper cleanup, marker, pattern and timeout behavior. |
| `ops/tests/test_simplification_contract.py` | Keep: status13 extends the stable public exit enum contract. |
| `ops/tests/workflows/test_create_admin.py` | Keep: actual local-key fixture adaptation and real PTY/shell bridge protect fixed command and secret isolation. |

### Verification and result

The broad audit removed 654 test/fixture lines and added 170 while preserving assertions and
moving coverage to its owner: 484 net lines fewer. Collection fell by 92 cases, from 2,593 to
2,501. Against `origin/main`, test additions are 10,516 lines and removals are 1,643 lines;
six unused fixture files no longer contribute to the branch.

Fresh independent review passed the semantic removal map and all 65 file dispositions. The full
operations gate passed with 2,478 tests and 23 skips; `mix precommit` passed with 1,210 tests and
32 exclusions. The documented fork/thread warnings remain. Opt-in native acceptance was not rerun.
These checks establish the reviewed local coverage, not universal test minimality or native host
behavior. No runtime saving is claimed without a comparable timing experiment.
