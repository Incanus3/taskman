# Operations test overlap review

Status: approved three removals delivered; pre-consolidation local build gates passed. Updated: 2026-09-17.
Tracking: completed mapping `tas-sr4b.28`; delivery `tas-sr4b.29`, parent `tas-sr4b`.

## Scope and baseline

This is the pre-merge test-overlap increment after the completed
[production simplification review](2026-09-16-operations-simplification-candidates.md).
Source checkpoint: `6153969bd7c8b153fe1e60a5093d9b8ac79d6538`, branch
`dedicated-host-deployment-automation`; workspace clean before mapping.
The inventory contains 72 test files and 1,010 test function definitions; parametrization
produces additional runnable cases. Full collection succeeds. This is a bounded overlap
review, not a full-branch defect review or proof that every duplicate has been found.

The [reconciliation design](../specs/2026-09-09-deploy-reconciliation-design.md),
[dedicated-host design](../specs/2026-09-09-dedicated-host-deployment-design.md),
[acceptance coverage map](../plans/2026-09-14-deploy-reconciliation.md#acceptance-coverage-map),
[development guide](../development.md#operations-verification), and
[accepted earlier coverage trade-offs](2026-09-16-operations-test-parallelism.md#accepted-history-coverage-split)
retain authority. Local checks do not establish native-host acceptance.

## Removal criterion

A removable test must exercise the same production boundary with equivalent scenario inputs,
setup and consequential assertions as a retained owner, or be strictly implied by that owner.
A matching name, assertion text or outcome across different execution boundaries is insufficient.
Do not collapse parser, controller, protocol, handler, entrypoint, native adapter or real packaged
consumer checks merely because they express the same product guarantee.

Discovery used a repository-wide AST body comparison and test inventory to find leads, then direct
inspection of parameter payloads, helpers, fixtures and production calls to prove the selected
local cases. The coordinator also compared identifier/archive admission, CLI/configuration,
package/materialization, dry-run, transport/output, firewall and systemd overlap. Separate read-only
workers compared controller/workflow versus packaged consumers and helper/protocol candidates.
The discovery workers supplied evidence; the coordinator owns the removal decision and rejected
the helper lead below. A distinct verifier reproduced and reviewed the three proposed local removals.
This is not an exhaustive assertion-by-assertion audit of all 72 files.

No timing benefit is promised. These small duplicates primarily obscure ownership; their focused
checks already take a fraction of a second. Do not grow the proposal merely to achieve a test-count
or runtime target.

## Confirmed local candidates

Paths below are repository-relative. Node IDs include the collected parameter suffix where relevant.

| Proposed removal | Retained owner | Complete assertion and scenario mapping |
| --- | --- | --- |
| `ops/tests/test_config.py::test_install_root_topology_rejects_overlap_and_reserved_path_collisions[overrides0]` | `ops/tests/test_config.py::test_two_root_paths_must_be_absolute_normalized_disjoint_and_reserved_free[backup_root-/opt/taskman]` | Both build the fresh default environment with `backup_root=/opt/taskman`, retain `install_root=/opt/taskman`, invoke `EnvironmentConfig.model_validate`, and require `ValidationError` without inspecting a message. No mutation, transport or lifecycle effect is exercised. |
| `ops/tests/test_config.py::test_install_root_topology_rejects_overlap_and_reserved_path_collisions[overrides1]` | `ops/tests/test_config.py::test_two_root_paths_must_be_absolute_normalized_disjoint_and_reserved_free[backup_root-/opt/taskman/backups]` | Same fresh baseline and production call; both change only `backup_root` to a child of the installation and require `ValidationError`. No additional assertion or effect. |
| `ops/tests/test_cli.py::test_each_stable_exit_status_has_the_documented_numeric_code` | `ops/tests/test_simplification_contract.py::test_every_public_exit_category_is_retained` | Both iterate the same imported `ExitStatus` enum and assert the identical twelve name/value pairs: OK=0, INVALID=2, LOCAL_PREREQUISITE=3, SECRET=4, REMOTE_PREFLIGHT=5, BACKUP=6, MIGRATION=7, RELEASE=8, READINESS=9, SAFETY=10, RESTORE=11, LOCKED=12. The retained exact tuple additionally constrains iteration order; it implies the removed dictionary assertion. Neither calls CLI dispatch or checks error translation. |

`two_root_environment(**overrides)` calls `valid_environment()`, applies exactly those overrides,
and returns the dictionary; `valid_environment(**overrides)` builds the same dictionary and applies
the same overrides. Neither function has state or side effects. No autouse fixture distinguishes
the configuration scenarios. The CLI file's secret-registry fixture does not affect enum iteration.

Bounded implementation would remove only the first two literal dictionaries from the topology
parameter list, retaining its other four scenarios, and delete only the redundant CLI enum test.
Keep both test files and the stronger public-contract enum owner. No production or test-support
change is needed. Parameter suffixes for the four remaining topology cases will renumber; review
by payload rather than carrying stale suffixes into later verification.

Lost coverage: two redundant invocations and one weaker assertion location. No supported input,
assertion, transition or execution boundary is lost for these three cases. Enum iteration already
omits aliases in both tests; alias detection is outside their current coverage and is not added
by this removal. All other configuration path cases and command-level exit propagation checks stay.

## Overlap that must remain

| Similar tests | Why they are distinct |
| --- | --- |
| Release identifier refusal in `test_manifests.py` and `releases/test_identifiers.py` | Bodies both call `validate_release_id` under `ValueError`, but parameter sets differ: uppercase revision, short revision and unsupported OS versus missing digest, old runtime, uppercase/short digest and dirty suffix. Keep both sets. |
| Unsafe archive member and extra runtime child tests in `test_manifests.py` | Identical bodies use different archive members: traversal/link/device safety versus the exact supported runtime layout. Keep both parameter sets. |
| Missing identifier, unknown/extra identifier and scoped consent tests in `test_cli.py` | Same `main` refusal assertion, different parser states and command/flag combinations. |
| Caddy foreign/ambiguous listener and authorized-PID refusal tests in `host/test_facts.py` | Same admission call and SAFETY assertion, but nginx/missing PID versus split PIDs/wrong service PID are distinct evidence failures. Keep every case. |
| Service-port and installation-root refusal tests in `test_config.py` | Same model/error assertion, different invariants. Only the two exactly repeated path payloads above qualify. |
| Package builder execution in `host_helper/test_package.py` and private-copy execution in `test_integration_packages.py` | The former executes freshly built archives; the latter exercises the immutable fixture's materialization, digest/mode, independent destinations and sibling mutation isolation. Keep construction and materialization owners. |
| Transient and scheduled package determinism/allowlist tests | Different executable consumers and authority allowlists. Keep both. |
| Firewall executable no-op and direct pyinfra-operation no-op | Native command behavior and adapter change reporting are separate boundaries. |
| Runtime environment exit-status receipt, actual writes and protected-failure reporting | Receipt semantics, idempotent filesystem/mode convergence, suppression and possible-mutation evidence differ. |
| Dry-run parser flags, CLI dispatch and real backup/deploy workflows | Flag parsing and no-native-mutation guarantees are separate boundaries. |
| Protocol budgets, stored records, archive targets and full-history observers | Limits and refusal policies belong to separate admission/publication boundaries. A codec pass cannot establish storage or full-history correctness. |

## Helper and protocol comparison

One suggested removal was rejected during synthesis: keep both
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

Read-only discovery compared workflow tests with all 29 end-to-end test function definitions
(48 collected cases). No same-boundary duplicate was proved in that comparison. Retain:

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

## Mapping verification before removal

Mapping reproduction completed without edits:

```sh
uv run --project ops pytest ops/tests --collect-only -q
uv run --project ops pytest \
  ops/tests/test_config.py::test_two_root_paths_must_be_absolute_normalized_disjoint_and_reserved_free \
  ops/tests/test_config.py::test_install_root_topology_rejects_overlap_and_reserved_path_collisions \
  ops/tests/test_cli.py::test_each_stable_exit_status_has_the_documented_numeric_code \
  ops/tests/test_simplification_contract.py::test_every_public_exit_category_is_retained -v
```

The focused run passed 17 cases in 0.13 seconds. Distinct scoped review found no blockers,
confirmed both configuration inputs and all twelve enum pairs against production/fixtures,
and independently reproduced 17 cases in 0.12 seconds. The four retained topology payloads
are `/opt/taskman/current`, `/opt/taskman/releases/backups`, installation `/var/lib/taskman`
and backup `/var/lock/taskman/backups`. Document paths/anchors and whitespace passed.
`mix precommit` passed 805 tests in 41.1 seconds. Live issue/export state is synchronized;
the handoff diff retires completed mapping instructions and retains every unfinished step-4
verification requirement and steps 5–6 authorization gates. These are original tests; no removal or
before/after runtime improvement is established. Any approved removal must reproduce retained
owners and the remaining topology cases, then run the full locked operations and `mix precommit`
gates. Check that collection decreases by exactly the approved number and that no helper,
parameter payload outside scope, or public packaged assertion disappears.

Step 4 also retains clean/dirty build and package checks. Use the runbook and development guide's
identified-checkout requirements before building; no host connection is required. These build
checks are not established by this mapping checkpoint. Separately authorized history consolidation
then requires an identity-sensitive rebuild, followed by separately authorized exact post-squash
real-VPS acceptance. The [handoff](../handoffs/ops-vps-readiness.md) preserves the complete sequence.

## Approved delivery checkpoint

The operator approved exactly the three mapped removals on 2026-09-17, with implementation
in this session. `tas-sr4b.29` owns delivery. The edit removes only those two parameter payloads
and the weaker CLI enum assertion. Affected collection changes from 168 to 165; the full suite
changes from 1,583 to 1,580. Retained focused checks pass 14 cases. The integrated four-worker
operations gate passed 1,580 tests with 158 known fork/thread warnings in 55.53 seconds.
Locked dependency sync, compileall, shell syntax and build help checks passed. The four distinct
topology payloads and the stronger twelve-pair enum owner remain; no production/support code
or public packaged assertion changed. Separate scoped delivery review passed: the topology test
body and all other configuration test ASTs are unchanged, only the approved enum test is absent,
and all retained CLI test ASTs/order are unchanged. `mix precommit` passed 805 tests in 43.5 seconds. Clean/dirty release build gates passed as recorded below.

## Pre-consolidation release evidence

Verified test-edit commit: `cfabead4738e`. Clean and controlled-dirty builds both used the
clean GitButler workspace revision `974d7a126a8c4b7a9e9a3084de54b4024db5fcf0`, with source stabilization
before building. The controller ran `./ops/taskman build --json` and
`./ops/taskman build --allow-dirty --json`, with a private external `TMPDIR` for receipts and
artifacts; neither command connected to a host.

| Artifact | Archive bytes | SHA-256 | Source state |
| --- | ---: | --- | --- |
| Clean | 44127405 | `4ff7f04245cd889d078de7d2cd16ef892ff2a7e894ffdccd6cf8a8fcff966f0c` | `source_dirty=false`, no dirty suffix |
| Controlled dirty | 44127405 | `413ecb664b637bfb236c286a497e44528360f857cab346f09f5238eadf12ebd0` | Same source revision, `source_dirty=true`, terminal `-dirty` |

Both complete archive/manifest/detached-checksum triplets passed production `verify_artifact`.
Both manifests use schema 3 and ten migrations, Ubuntu 26.04/amd64, Elixir 1.20.4,
OTP 29.0.6, Node 22.22.1, Hex 2.5.1 and Rebar3 3.24.0. Their builder identity matches
`ubuntu:resolute-20260811.1` and
`sha256:2260313b31c8c011cd2eebe728008efac1b3982be73eb71348ea2648d2c0e09b`.
Archive, manifest and checksum modes are 0600; artifact directories are 0700. The builder's
network-isolated runtime configuration eval succeeded on the pinned VM without starting Taskman.

During the dirty build, direct inspection of its frozen source confirmed the controlled README
change and absence of an ignored `tmp/` canary. Both temporary inputs were restored/removed;
the checkout returned to its exact clean baseline. Before and after the dirty build,
`identify_clean_inputs` and `resolve_deploy_target` selected the same verified clean artifact
with source `cached`, no installed records and a builder callback that fails if called.
The dirty artifact cannot satisfy the clean-input match.

This completes step 4's bounded removal and pre-consolidation local build gates. The suite
runtime difference is not a demonstrated optimization. No host, future push, merge or history
rewrite occurred. These artifacts establish the named pre-consolidation inputs; documentation
closure and later history consolidation change source identity. The exact post-squash build and
identity-sensitive checks remain required before separately authorized real-VPS acceptance.
