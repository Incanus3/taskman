# Operations test overlap review

Status: completed coverage-decision record. Updated: 2026-09-18.
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
The [acceptance report](2026-09-17-operations-vps-acceptance.md#closing-artifact-and-source-verification)
owns the identified build receipts.

## Removal criterion and method

A removable test exercises the same production boundary with equivalent inputs, setup and
consequential assertions as a retained owner, or is strictly implied by that owner.
Matching names, assertion text or outcomes across different boundaries are insufficient.
Parser, controller, protocol, handler, entrypoint, native adapter and actual packaged-consumer
checks retain separate ownership. No test-count or timing target justifies extra removal.

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
