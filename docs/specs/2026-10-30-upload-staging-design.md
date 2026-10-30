# Separate upload staging from deployment records

Status: proportional upload-check revision D5 implemented locally, independently reviewed and operator accepted. The deployed baseline still implements the superseded D3 hardening.

## Decisions and approval scope

| ID | Status | Choice | Cost or constraint | Rationale |
|---|---|---|---|---|
| D1 | Accepted | Derive uploads directly beneath `install_root`, beside `deployments` | Changes controller and host path validation together | [Layout and alternatives](#layout-and-alternatives) |
| D2 | Accepted | Clean existing installations once; add no legacy recognition or automatic migration | Old controllers can recreate the obsolete directory | [Existing installations](#existing-installations) |
| D4 | Accepted | Remove only a validated empty old directory during a coordinated quiet window | Unexpected contents require investigation and a separate disposal decision | [Existing installations](#existing-installations) |
| D5 | Accepted | Retain ordinary upload checks and existing transfer machinery; drop protection against concurrent replacement of validated directories | An administrator must not replace upload-path components during an operation; final writes no longer retain validated directory descriptors | [Simplified upload checks](#simplified-upload-checks) |
| D3 | Superseded by D5 | Prepare uploads with retained directory authority and exclusive descriptor-relative copying | Added traversal/copy machinery and a large test matrix without a demonstrated normal-workflow need | [Simplified upload checks](#simplified-upload-checks) |

D5 supersedes D3’s retained-directory-authority and custom-copy guarantees. D1, D2 and D4 remain accepted; the completed one-time cleanup is not repeated. The existing deployed code still provides D3’s broader guarantees until the locally accepted simplification is separately deployed.

Approval of this document authorizes implementation planning. It does not authorize implementation, publication, deployment or host cleanup. After plan approval and the implementation-session boundary, implement and independently verify the bounded change. Present exact target installations and concrete cleanup commands for separate operational authorization before touching hosts. Completion requires operator acceptance, including the agreed installation cleanup or an explicit decision to defer it.

## Evidence and inherited constraints

Repository-relative Task 36, verified against description marker `Source: taskman/br/tas-jady`, owns this work. Its original remedy was to recognize `deployments/uploads`; the operator selected relocation and one-time cleanup instead.

Before relocation, inspection on 2026-10-30 established:

- [`workflows/helper.py`](../../ops/taskman_ops/workflows/helper.py) stages release archives and scheduled-backup helper packages beneath `config.deployment_root / "uploads"`. It removes exact uploaded files after a finalized helper result; uncertain dispatched transport failures can retain them.
- [`host_helper/state.py`](../../ops/taskman_ops/host_helper/state.py), `_note_unknown_deployment_entries`, warns about every unrecognized deployment-root entry, including the controller-created directory.
- [`operations/deploy.py`](../../ops/taskman_ops/host_helper/operations/deploy.py) and [`backup_helper.py`](../../ops/taskman_ops/host_helper/backup_helper.py) constrain uploaded inputs to that derived location. Backup-helper installation also checks filesystem components without following symlinks.
- [`remote.py`](../../ops/taskman_ops/remote.py), `Remote.put`, has a separate private SSH transfer stage. This design changes the final managed upload destination, not that transport stage.

The [deployment design](2026-09-09-dedicated-host-deployment-design.md#configuration-and-path-authority) owns the current derived layout. Its two configurable roots, private upload handling, immutable releases and lifecycle lock remain constraints. The [operations contracts](2026-09-18-operations-contracts.md) own request/result, cleanup and recovery semantics. Do not import parked admission-lock or provisioning-recovery changes into this work.

Historical tracker observations were insufficient cleanup authority. Native acceptance used fresh inspection of each old directory, as summarized below; real inventories and receipts remain private.

## Layout and alternatives

Use this derived layout, illustrated with standard project paths:

```text
/opt/taskman/
  releases/       immutable installed releases
  deployments/    durable selection, protection and restore records
  uploads/        transient release archives and helper packages
  current
  lifecycle.lock
```

Keeping and validating `deployments/uploads` is the smallest diagnostic fix, but mixes transient transfer inputs with durable record inventory. Relocating it separates those responsibilities. Automatic legacy migration would add permanent upgrade logic and require decisions about uncertain retained inputs. The operator selected relocation with one-time installation cleanup instead.

Do not add an upload-root configuration option, path alias, fallback, legacy allowlist or automatic old-directory removal. The deployment inventory continues to warn about a retained old directory through its existing unknown-entry behavior.

## Upload authority

Add a derived `upload_root = install_root / "uploads"` property to controller `EnvironmentConfig` and host `ManagedPaths`. All release and backup-helper upload producers and consumers use these properties. Host request roots remain exactly `install_root` and `backup_root`; no new root selector or wire field is needed. Controller and transient helper are distributed together; new code rejects old-location inputs through existing derived-authority validation.

Prepare the upload root before any release or helper upload, including restore and installed-release reuse that needs only a replacement backup helper. Absence is allowed during read-only inspection. Read-only commands must not create it. Existing unsafe roots must be refused rather than repaired: validate trusted ancestors and reject symlinks, non-directories, wrong ownership or unexpected permissions before privileged upload. Newly created upload roots are root-owned, group root, mode `0700`; final uploaded files remain root-owned regular files, mode `0600`. Host consumers revalidate the root and existing file authority before use. Preserve checksum, archive-member, helper-package and path-containment validation.

Under D5, every existing path component from `/` through `install_root` must be a real root-owned directory with no group/other write or special permission bits (`mode & 0o7022 == 0`). Existing upload roots must additionally have group root and exact mode `0700`. Inspect existing components with ordinary `lstat` checks. Retaining ancestor directory descriptors across later access is not required; concurrent manual replacement of validated components is outside the supported guarantee. Only the derived installation/upload directories may be created when absent, using their established installation permissions and private upload permissions respectively; arbitrary missing ancestors are not created. This intentionally refuses installations beneath user-owned, writable or symlinked ancestors. Disposable tests may inject synthetic ownership authority without weakening production checks.

Before any preparation mutation, unsafe existing metadata produces controller safety status `10`, stage equal to the dispatch operation (`deploy`, `genesis`, `prepare_genesis` or `restore`), with message `upload staging path is unsafe`. Creation/access failures produce status `8`, stage `release`, for deployment/provisioning dispatch, or status `11`, stage `restore`, for restore dispatch, with message `unable to prepare private upload staging`. Do not dispatch or upload after failure. Retain any prior known/possible mutation evidence, including directory creation; never report an operation unchanged merely because no archive was transferred. Host-side revalidation failures use the existing operation's input/authority error mapping and result shape. No protocol change or new failure category is introduced.

Only paths derived beneath the new root are eligible upload inputs. Preserve each consumer's additional framing rules, including direct-child `.pyz` inputs for backup helpers. Do not broaden accepted input shapes while changing the base path.

Preserve unique per-request filenames, exact-file cleanup and cleanup warnings. A finalized helper result permits removal of that invocation's files. A dispatched operation with uncertain completion retains inputs under the existing contract. Never recursively clear the upload root or treat retained files as an artifact cache. No changes to the SSH transport stage, release extraction staging, backup retention, database state, selection records or scheduler semantics are intended.

## Existing installations

Document a one-time operator procedure in the deployment runbook, not a new public CLI command or persistent migration routine. The coordinating agent performs this procedure directly after concrete target/command authorization; the ops machinery contains no legacy cleanup logic:

1. Identify the exact installation roots and all controllers capable of operating them. Arrange a quiet window: no provisioning, deploy, restore, cleanup or other competing operation. Switch active controllers to the corrected version before resuming operations. The lifecycle lock alone is insufficient because uploads precede helper lock acquisition.
2. Inspect the old directory without following symlinks, applying the component authority checks through its parent, then requiring root ownership, group root, mode `0700` and emptiness. Missing means no cleanup is needed. A symlink, unexpected owner/mode/type, or any contents stops cleanup for inspection. Do not repair metadata, move files or delete leftovers automatically.
3. For a validated empty directory, use exact-path `rmdir` under the established quiet window. Do not use recursive deletion or wildcard removal. If removal fails or contents appear, report failure and preserve remaining state; do not escalate to forced removal. Verify the old path is absent.
4. Run read-only deployment inventory/verification with the corrected controller. Verify the specific old-directory warning is gone while preserving unrelated warnings. Exercise new uploads during the separately authorized deployment acceptance; record private target/version/result evidence.

Do not stop backup timers merely to remove an empty upload directory. Coordinate any overlapping lifecycle activity, without changing timer policy. This procedure does not authorize deletion of recovery material or unattended-controller termination.

| Situation | Observable behavior |
|---|---|
| Fresh installation | Upload-producing operations create the new private root; no old directory is created |
| Existing installation, old directory empty | Updated operations use the new root; inventory warns until the separately executed cleanup removes the old directory |
| Existing installation, old directory contains files | Cleanup stops; files remain for explicit investigation/disposal decisions |
| New upload root is a symlink or has unsafe metadata | Operation refuses before uploading through that root |
| Upload completion is uncertain | Existing retention/error semantics apply at the new location |
| Older controller is used afterward | It can recreate the old directory; upgrade that controller and reassess cleanup rather than adding compatibility logic |

## File boundaries and verification

Expected implementation boundaries:

- Controller configuration, host `ManagedPaths`, upload preparation in `workflows/helper.py`, and upload authority in host deploy/backup-helper consumers. Reuse `Remote.put`'s existing private transfer stage and native final `install`, after ordinary upload-root/destination checks; remove the added managed-copy route. The separate private SSH transfer stage remains unchanged.
- Focused tests under `ops/tests/workflows/`, `ops/tests/host_helper/`, path/config tests and representative end-to-end upload/cleanup tests. Update existing fixtures using the old path where they describe current accepted inputs.
- Current layout in the dedicated-host deployment specification, upload contract in the operations contracts, and the one-time cleanup procedure in `docs/guides/deployment.md`. This specification owns the relocation decision and trade-offs.

Acceptance requires focused coverage of fresh deployment, installed-release helper replacement and restore uploads; refusal of unsafe new roots and old-location inputs; unchanged successful, failed and uncertain cleanup behavior; and deployment inventory warnings for unrelated/retained old entries. Cover the empty-directory cleanup procedure with disposable local filesystem cases, including nonempty directories and symlinks, without adding production migration machinery.

Run the operations compile, pytest and shell checks plus `mix precommit` required by the development guide. Check documentation links, whitespace, privacy and leaked planning terminology. Obtain scoped independent correctness/security review of path preparation, consumer validation and cleanup instructions. Local checks do not establish native host acceptance. Operational evidence must separately establish new-path use, safe removal of each old empty directory and disappearance of its warning.

## Implementation and acceptance

Scoped independent source review accepted the relocation and cleanup approach. Its metadata/failure-semantics clarification is incorporated in D3; a follow-up source review found no blocking issue and confirmed standard fresh provisioning creates compatible installation directories. Review also identified the final managed-destination write as part of D3's implementation boundary. The operator approved the complete contract. Documentation links and whitespace are checked separately from runtime acceptance.

The deployed D3 baseline derives controller and host upload roots from the installation, guards final writes and consumer reads with the shared descriptor-relative capability, and keeps controller preparation mutation separate from raw helper evidence. D5 replaces the additional traversal/copy machinery; the following results establish the earlier deployed baseline and do not validate D5 behavior. The transient helper packages that capability; existing scheduled-helper packaging remains sufficient. The [deployment runbook](../guides/deployment.md#remove-an-obsolete-empty-upload-directory) owns the exact one-time cleanup commands and disposable-fixture reproduction limits.

Local verification of the implementation passed locked dependency synchronization, Python compilation, shell syntax checks, and the full operations suite: 2,640 passed, 30 skipped, with 179 known fork/thread deprecation warnings. `mix precommit --max-cases 8` passed 1,395 tests, with 3 skipped and 51 excluded. A later test-only review correction passed all 25 backup-helper tests and now proves its intended interrupted write and primary error. Changed canonical-document links/anchors, whitespace, scoped privacy and planning-terminology checks passed. Independent scoped correctness/security review accepted both checkpoints and their combined session result through `455b73cc` with no unresolved findings. Task threads retain the bounded implementation/review evidence.

Authorized native acceptance used controller/artifact source revision `b9b1b27a` on two existing installations. Both deployment previews required no migrations or downgrade acknowledgment. Release and replacement backup-helper uploads completed through the new root; before/after inspection established creation of root/group-root `0700` upload directories and removal of finalized inputs. Both deployments selected and ran the expected release, passed local/public readiness, release identity, listener topology, service, startup-journal and HSTS checks, and preserved enabled/active backup timers.

During a coordinated quiet window, fresh authority/emptiness checks passed for both old directories. The coordinating agent executed the runbook's exact empty-directory removal directly; both old paths were absent afterward. Final read-only verification passed for both installations and the specific old-directory warning disappeared, with no remaining warnings. Exact targets, artifact/checksum, commands and receipts remain in private operator storage outside version control. This bounds acceptance to those installations and actions; it does not establish ongoing disaster recovery or authorize unrelated operational work.

The operator accepted the implementation and native results on 2026-10-30. No autonomous implementation rulings required acknowledgement. The temporary implementation plan and workstream handoff are retired; this specification and the deployment runbook retain the contract, rationale, operating procedure and verification limits.

## Simplified upload checks

D5 reduces the additional hardening introduced by D3 while preserving relocation and ordinary safety checks. The operator approved the written revision after reviewing the complexity cost. The simplification is implemented, locally verified and operator accepted; independent scoped review passed with no actionable findings. No D5 deployment has been authorized or performed.

### Approaches and reason for the change

| Approach | Benefit | Cost and disposition |
|---|---|---|
| Keep D3's retained directory authority and exclusive descriptor-relative copy | Access remains attached to validated objects despite concurrent pathname replacement | Keep custom traversal/copy, remote copy dispatch and their tests; rejected in discussion because no supported ordinary workflow requiring that protection was established |
| Use ordinary checks with the existing private SSH stage and native final `install` | Preserve practical input/permission/confidentiality checks with substantially less new machinery | Selected in discussion; a successful check does not protect against an administrator replacing its ancestor directories before later access |
| Keep only the path change | Smallest code change | Rejects too few ordinary unsafe configurations and inputs; not selected |

The repository assumes trusted operators and handles ordinary overlap through existing lifecycle coordination. No normal supported operation replaces the installation/upload ancestors during an upload. D5 explicitly excludes deliberate or concurrent manual replacement of validated path components. It does not promise metadata-repair or race-recovery behavior for that excluded activity.

### Checks and behavior retained

- Keep `upload_root = install_root / "uploads"` on controller and host. No legacy recognition, fallback, automatic migration, new root selector or protocol field.
- Before every upload-producing request, inspect existing components from `/` through `install_root` with ordinary `lstat` calls. Require real root-owned directories with `mode & 0o7022 == 0`; require the existing upload leaf to be root/group-root and exactly `0700`. Reject symlinks, wrong type/ownership/mode and unreadable authority instead of repairing existing metadata.
- Create only a missing installation leaf (`0750`) and upload leaf (`0700`); do not recursively create arbitrary ancestors. Set required metadata only on directories this invocation creates, then validate the resulting paths. Check a colliding directory as existing state, without repairing it. Directory descriptors need not be retained across these steps.
- Compute all release/helper destination names before preparation and pass their basenames as separate arguments to the preparation command. Before any preparation mutation or transfer, reject any already-present final destination, including a symlink. Unique per-request names remain. A concurrent manual creator after that check is outside D5's guarantee; native `install` is not described as exclusive creation.
- Reuse the existing `Remote.put` private SSH transfer stage, native final `install` with `0600`, and exact transfer-stage cleanup. Do not add a managed-upload keyword, custom byte-copy loop or copy action to that transport API.
- Host consumers recheck upload-root authority, lexical containment and existing consumer-specific framing. Preserve regular-file/root-owner/exact-`0600` checks, no-follow opening of the file itself, checksums, size/archive validation, and backup helper's direct-child `.pyz` rule. Nested release inputs keep their existing accepted shape; inspect intermediate relative directories without following symlinks.
- Hash, inspect and extract the same opened archive stream; close it on every success, early-return and failure path. This small consumer-local guarantee does not imply retained ancestor-directory authority. Preserve the preexisting scheduler executable's validation and atomic replacement behavior.
- Prepare once for release uploads and helper-only installed reuse/restore; no-input reuse and read-only inspection create nothing. Preserve exact-file cleanup after finalized helper results and retention after uncertain dispatched completion, including existing warning precedence.
- Treat a failed or interrupted ordinary upload conservatively as possible managed-file mutation: generic `Remote.put` failure is not proof that native final `install` left its destination unchanged. Honor explicit proof that failure preceded managed destination access; a bare `changed=False` from the generic transport is not that proof. Aggregate `unknown` upload evidence with any earlier known preparation change in the upload workflow; keep `Remote.put`'s existing generic signature/behavior for other consumers. Do not restore a managed-copy dispatcher solely to report this uncertainty.
- Retain truthful preparation mutation reporting separately from raw helper/database observations. Keep the existing `UploadDispatchResult` and required aggregation rather than removing working evidence handling solely to reduce line count. Preserve the current public status/stage/message mapping documented above. Private preparation may continue the existing exit-only `0/3/10/1` convention because sensitive command output is suppressed; remove the separate copy dispatcher, not required preparation error reporting.

### Implementation boundaries and verification

Put the small shared pathname validation/preparation functions in the existing stdlib-only `host_helper/paths.py`, with remote preparation invocation and public error mapping in `uploads.py`. Keep source reuse for that preparation rather than implementing a second validation algorithm in shell. Remove `host_helper/upload_files.py`, its transient packaging entry, `Remote.put`'s managed route and `_install_managed_upload`. The controller may execute the stdlib path code plus a preparation-only entry snippet; no permanent host installation or public command is added. Do not move the descriptor framework unchanged into another file.

Release reads stay local to deployment artifact validation. Backup-helper upload reads reuse their preexisting file-opening/checksum machinery with the derived root checks; leave the scheduler destination's preexisting descriptor-relative atomic replacement intact. Update workflow transfer calls explicitly to the existing `Remote.put` signature. Update the deployment design, operations contracts and runbook where they currently promise D3's stronger access guarantees. The already executed one-time cleanup is complete; do not rerun it as part of this change.

Keep tests for derived paths, unsafe root/file metadata, symlinks, containment and framing, collisions observed before transfer, helper-only uploads, checksums, same-opened-archive use, descriptor closure, public error mapping, mutation evidence and cleanup/uncertainty semantics. Remove tests whose sole contract is survival of concurrent directory replacement or the removed custom transport route; do not transplant that matrix into the simpler helpers. Add focused tests for the production pathname-check and native-transport wiring. Existing isolated helper archive tests must pass under `-I -S` with no import of the removed module.

Run focused affected tests, locked dependency synchronization, compileall, the full four-worker operations suite, shell syntax, `mix precommit --max-cases 8`, changed-document links/anchors, whitespace and scoped privacy/terminology checks. Obtain independent review against D5's retained guarantees and the reduction in machinery; passing checks under D3 do not validate the changed D5 implementation.

Local D5 verification passed locked dependency synchronization, Python compilation, shell syntax and the full four-worker operations suite: 2,621 passed, 22 skipped, with 179 known fork/thread deprecation warnings. `mix precommit --max-cases 8` passed 1,395 tests, with 3 skipped and 51 excluded. Focused consumer and isolated-package checks passed 371 tests; the core/path/native-transfer and consumer-open regression checks passed 142 tests. Changed-document links/anchors, whitespace, scoped privacy and planning-terminology checks passed. Independent scoped correctness/security review accepted the simplification at `9e74eaf8` with no actionable findings. The operator accepted local delivery and workstream closure on 2026-10-30. These results establish local behavior only; no D5 native deployment acceptance is claimed.

The local simplification is on branch `upload-staging`, with PR 43 open against `main`; its code, tests and operating contracts implement the accepted revision. Root Task 36 and its original children remain Done; the standalone simplification follow-up, repository-relative Task 64 (`Source: taskman/upload-staging/simplification`), is Done after operator acceptance. The temporary simplification plan and handoff are retired; this specification and the operating-contract documents retain the accepted behavior, rationale, verification and limits. Publication of review corrections and any native redeployment require their own operator authorization; the previous deployed artifact remains the D3 baseline until an authorized corrected deployment.
