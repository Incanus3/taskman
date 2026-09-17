# Disposable VPS operations acceptance

Acceptance findings recorded on 2026-09-18. These are identified test results, not live host
state or acceptance of later source changes. The [runbook](../guides/deployment.md) owns procedures;
the [architecture](../specs/2026-09-09-dedicated-host-deployment-design.md) and
[operations contracts](../specs/2026-09-18-operations-contracts.md) own behavior and safety rules.
Environment obligations belong to the [inventory](../inventories/operations-environments.md).

## Scope and evidence boundaries

Acceptance used disposable staging `taskman.page` through root SSH alias `deploy`, with no
production data. Compatible OS services/packages were retained. Unsupported legacy formats were
excluded, not upgraded or converted. Administrator and invitation mail used two distinct recipients
recorded only in protected private evidence.

| Baseline | Evidence established | Qualification |
| --- | --- | --- |
| Authenticated retained database: source `3f11dde0096467d0bea1fc7128d6eb40dbda80a9`, archive SHA256 `be8e91bbc2b1a366a7f46283b039bf7b2269d967fb23cb1f2e2eb290aa8fe356` | Administrator/invitation/recovery, API/CLI/LiveView parity, backup/off-host copy, rollback/forward, migration refusal/recovery, typed restore and bounded leakage checks. | Accounts/data are archived recovery material, not the fresh installation. |
| Fresh installation: source `4ac7b24ce9f709106a03a3126afd7b4e32b054f2`, archive SHA256 `87e40fd49875b688e23c193b62d8b02c0a5410796265fdea0885a71c0ce05b23` | Absent role/database creation, first provision, unchanged rerun and eight native readiness checks. | Retained compatible OS services; database empty with no administrator. |
| Closing source `36c68bac7627b763a07d7570640f84115e67ac9c`, archive SHA256 `079e83b03cd0e83a05814d351f0e6be2846c9faba61271da058385a4d01bf002`; published head `d6369f3156ac9f0776891af3155210ac64129d72` | Exact source/artifact binding, packaging/cache/runtime/helpers, native dry-run and exact-head CI. | Closing artifact was not selected on the host. Later documentation changes are outside this artifact/CI proof. |

## Native findings and final behavior

These findings explain final implementation and consequential test boundaries. All are resolved
unless marked pending; issue-level development history is not part of this report.

| Finding | Final behavior and evidence |
| --- | --- |
| Ubuntu merged `/usr` can make genuine Caddy FragmentPath differ from dpkg's `/lib` registration. | Supported aliases require resolved-file equality plus package/metadata/md5 authority. Executed shell regression and native clean package probe passed. |
| Shared SSH sockets can have multiple valid owners without unique ownership. | Parser admits multiple valid owners; malformed records and managed-listener ambiguity refuse. Native facts/dry-run admitted shared SSH while retaining Caddy authority. |
| Native PostgreSQL catalog access and SQL syntax differ from mocked assumptions. | Pristine inspection uses legal `catalog_collation` and public `pg_user_mappings`, without grants. Exact native SQL accepted pristine=1 and refused a rolled-back public-function fixture=0. |
| Absent systemd timers and restrictive umasks need explicit handling. | Absence uses LoadState; a new lock parent is 0755 despite umask, existing conflicts are preserved and lock mode is 0600. Native discovery/convergence passed. |
| Empty package-track arguments, command-mode SQL variables and unchecked listing failures cannot establish database authority. | Nonempty `*`, checked cluster listing and protected stdin `psql --file=-`/ON_ERROR_STOP retain cluster/role/membership/owner refusals. Native complete dry-run passed. |
| Free-space counters can change between snapshots without material authority drift. | Only two volatile counters are excluded from material comparison; reported facts and fresh capacity thresholds remain. Native unequal raw snapshots had equal material projections; original refreshed snapshot was not retained. |
| PID-file startup seconds and SQL start time can differ by one second. | SQL backend PPid must equal PID-file line 1; line-3 validation and port/data/cluster/config/HBA checks remain. Native inspection/convergence passed. |
| systemd StateDirectoryMode must agree with the private home. | Owned home/state mode is 0700; RuntimeDirectoryMode remains 0750. Retry, repeat provision and reboot preserved the boundary. |
| Safe application/distribution listeners can appear after the initial topology sample. | Wait only for absent safe listeners within the shared global 45-second readiness budget; malformed/public/EPMD/missing-PostgreSQL snapshots fail immediately. First deployment passed without fallback retry; sampler measured about 6.85 seconds of safe delay. Original failing synchronous sample was not retained. |
| A healthy 100-line journal can exceed the generic output budget. | Journal alone permits 65,536 bytes per stream, retaining whole failure matching, nonempty evidence and 3-second command/global 45-second limits. Actual 11,907-byte journal, exact-limit, overflow and late-failure checks passed; other commands retain the 9,216-byte limit. |
| Invitation completion requires password input; recovery redemption requires `password_reset` purpose. | Project setup form/controller invokes Accounts.complete_setup without installing a session; recovery issuance uses the existing controller/context. Genuine activation/reset passed without accepting wrong-purpose tokens. |
| Restore preflight/termination require file-input SQL variable expansion. | Three SQL owners use protected bounded stdin/`--file=-`, retaining variables/authority/deadlines/ON_ERROR_STOP. Native preview and complete restore passed. |
| Canonical-only TCP HBA/pgpass does not authenticate derived restore databases. | Canonical observations remain application-authenticated; exact derived observations use local administrator authentication. Root opens the 0600 dump before peer-authenticated `pg_restore --role=<application-role>`. Native descriptor/load/swap/cleanup and restored data/account checks passed; trusted-backup limitation below remains. |
| Authentication cannot be required against genuinely absent application identities. | Only both role/database absent permit creation; incompatible presence and retained-role/missing-DB refuse. Exact record/root-owned 0600 file/byte authority and ready authentication remain. Native UUID creation/SCRAM, public packaged admission and first/repeat provision passed. |
| Controller provisioning convergence is outside the helper lifecycle lock. **Pending coverage extension.** | Fresh checks do not serialize pyinfra/database/runtime writes. The [lock proposal](../specs/2026-09-18-provisioning-lock-coverage-proposal.md) owns the reviewed admission/recovery design, with full design/plan approval and implementation pending in its dedicated post-merge workstream; future coverage is not established here. |

PostgreSQL source evidence: [public mapping view](https://www.postgresql.org/docs/18/view-pg-user-mappings.html),
[psql file/stdin parsing](https://www.postgresql.org/docs/18/app-psql.html),
[postmaster initialization](https://github.com/postgres/postgres/blob/REL_18_6/src/backend/postmaster/postmaster.c),
and [PID lock-file initialization](https://github.com/postgres/postgres/blob/REL_18_6/src/backend/utils/init/miscinit.c).

## Authenticated retained-database acceptance

The archive was 44,134,095 bytes; manifest/checksum/pins/private modes/cache and exact allowlisted
protocol-3 helper bytes passed, including both isolated `-I -S` entrypoints. Application code and
ten migration bodies matched recovery source `ad9c9523e2e7e8d725fe0d51faf8c1fc9e37113b`, archive
SHA256 `31d28056db2a1c4815713a67bfb27f481c89a21dfa4b484920204de8ce4aa380`.

Native provision, repeat, deployment without fallback retry and reboot passed. Reboot changed boot
ID; taskman-owned nonsymlink home remained 0700 and service active without restarts. HTTPS/readiness/
HSTS passed; 4000/5432/6789 were loopback-only, EPMD 4369 absent. UFW denied incoming by default,
allowed 22/80/443 and denied managed ports for IPv4/IPv6.

Hidden-password PTY/SSH administrator creation, browser login and administration passed. Invitation
mail origin/sender/recipient/time window were validated privately; setup activated/confirmed a
nonadministrator without creating a session, then those credentials signed in. Recovery mail/reset
passed: old password refused, replacement signed in and administrator authority retained. Positive
native CSRF submission passed.

Account Settings created a once-visible API key. Genuine Req HTTPS returned 401 for absent/invalid
bearer and 200 for the key. Genuine CLI with private 0700/0600 XDG configuration created/read
Project/List/Task data; browser relationships/status/description matched. Browser autosave read back
through CLI with other fields unchanged. Home/Project/Task used connected native WebSockets
(readyState=1), without fallback connections.

### Backup, rollback and migration recovery

Manual `backup-48a90e98225f4ab0b735688475b5f1fc` bound recovery source/archive and ten migrations.
Off-host plaintext was 25,914 bytes, SHA256
`653c5d27eeb6e9d6b75a6558f7cdd8c7045412623370b9a276956418291bfd83`.
Production BackupRecord/custom PGDMP/checksum validation, age encryption, identical decrypted bytes
and PostgreSQL 18.4 `pg_restore --list` for the native 18.6 dump passed. Independent checks reproduced
record/hash/private-mode/age-header/users-TOC evidence. This proves acceptance-copy transport and
integrity, not durable disaster recovery.

Typed rollback to predecessor source `5ab99a3658f9681e82fa340538184dc4eb710107`, archive SHA256
`e7f0520db3198d5aef1a7a38a3312498d463f73ecb913abe4f49019e821a6d4c`, and forward deployment
passed all eight checks and retained the Task baseline. Targets used validated release/backup/
completed-selection authority; fresh backup `backup-0d2e43e4db8c40569931d3cd42426d19` was retained.

A controlled dirty refusal fixture used source `fd31e06dad2a9f99ccf8c807a886a0cc608a402b`, archive
SHA256 `0e68bdc37287d6b773129963f0ea35ac0247ddbe97e1d85c84c6df1c5bcd1ea8`. Ten prior migrations
were identical; extra version 20260917203909 raised before schema/data mutation. Deploy returned
exit 7/migration-failed/changed=true: service stopped, database ready, ten migrations and successful
selection retained, `backup-ea300d9ca504498d9adb126b52ea00fa` protected. Exact clean recovery
passed all eight checks without pruning. The fixture has no clean-cache claim.

### Native restore and authentication

Root-open dump FD0 and peer postgres authentication with `--role=<application-role>`,
`--exit-on-error`, `--no-owner`, `--no-privileges` and existing 60-second/output/refusal bounds
passed on the actual VPS. Exact name/OID/owner/writer/template and dump/source/checksum authority
remained required; no authentication wildcard, credential copy or permission widening was used.
Separate native UUID checks proved current_user=application, session_user=postgres, application
object ownership, restored data and unchanged dump mode; container/runuser adapters alone were
not treated as full VPS proof.

Typed same-backup restore returned 0/restored/changed=true and all eight checks on the backup-source
release. Canonical OID was 16941; temporary/retired names and restore binding were cleaned through
supported success. Public tables/partitions/sequences had zero nonapplication-owned relations;
pgpass remained root-owned regular 0600. This does not establish ownership of every database object.
Task API/CLI equaled the saved pending/description baseline; a Task created after backup returned
CLI exit 3/not_found. Original API key and both account identities/administrator flags survived.
Standalone verification and forward deployment passed all eight checks.

Administrator authentication can regain privileges through RESET ROLE: this procedure uses trusted,
root-managed validated backups and is not a hostile-dump sandbox.
[pg_restore role switching](https://www.postgresql.org/docs/18/app-pgrestore.html) and
[SET ROLE identity semantics](https://www.postgresql.org/docs/18/sql-set-role.html) support that limit.
The [restore contract](../specs/2026-09-18-operations-contracts.md#authentication-for-restore-databases)
owns accepted behavior and alternatives.

## Fresh installation acceptance

The identified archive passed independent source/member/mode checks, pinned tools, ten migration
fingerprints/5,228 members, builder-forbidden cache reuse and both isolated helper consumers.
With documented external age identity/SSH agent, each command exited 0:

```sh
./ops/taskman provision staging --artifact EXACT_ARCHIVE --dry-run --json
./ops/taskman provision staging --artifact EXACT_ARCHIVE --yes --json
./ops/taskman provision staging --artifact EXACT_ARCHIVE --yes --json
./ops/taskman verify staging --json
```

Dry-run was mutation-free with absent identities/history/releases, ten pending migrations and no
downgrade baseline. First provision created strict role/database/protected configuration/scheduler,
applied ten migrations and selected the artifact. All eight checks passed (service/release/Caddy/
topology/journal/local/public/HSTS); database OID was 17213.

Repeat reported already-provisioned, top-level/release changed=false and mutation unchanged.
OID 17213/PID 60741/protected bytes/modes/target/migrations/services/listeners were identical;
no extra backup/selection. Independent standalone verification passed all eight checks. Direct checks
proved SCRAM, exact role flags/no memberships, empty users/projects/lists/tasks and application-owned
public tables/sequences. Protected environment/pgpass/backup env were root:root 0600; release/
executable root:taskman 0750 and completed manifest root:taskman 0600. Managed listeners were
loopback-only, 4369 absent, UFW allowed public 22/80/443; independent HTTPS proved TLS/200/sign-in/
HSTS. Installed/embedded manifests and ten migration hashes agreed; recovery files retained exact hashes.

This proves fresh Taskman installation on retained OS services, not pristine OS/package/ACME/firewall
creation. Database was empty with no administrator; authentication/email/backup/rollback/restore
proof belongs to the retained-database baseline. Refresh before use and perform fresh account setup;
do not reuse archived passwords, signed links or bootstrap data.

## Closing artifact and source verification

The closing source/archive passed manifest/checksum/ten migrations/pins/private modes,
builder-forbidden cache reuse and both actual isolated consumers. Independent artifact/runtime/
privacy/binding inspection found no material issue. Published tree was
`99f8c1c692c8e164640fb16056c2a46fe5915e0a`;
[exact-head CI run 35320423947](https://github.com/Incanus3/taskman/actions/runs/35320423947)
passed on 2026-09-18 at 07:42:55 UTC. These results do not cover subsequent changes.

| Packaging input | Source | Archive SHA256 |
| --- | --- | --- |
| Clean consolidated tree | `10b5a6b0193e72b4eec81ec66edd5b741b4be8ec` | `2512b4fa98feead0ecb20a30c9d7f96683193fde4349487e581c720ef261a485` |
| Controlled dirty isolated clone | Same revision plus private untracked fixture/dirty suffix | `273050e8c6c2e831a213634fcbd65b8a1318e6873867e2ca2c77ae27cda47591` |

Both inputs passed manifest/checksum/canonical migrations/pins/safe members/private modes and
private-fixture exclusion. Clean synthetic runtime eval passed without starting the application or
using real credentials; cache reuse passed with builder forbidden. Actual `-I -S` consumers returned
host protocol-safe status 0 and backup missing-config status 2, without missing imports. Extracted
terminal checks passed two cases without skips/local fallback: hidden input and TTY echo/canonical
restoration on Elixir 1.20.4/OTP 29/ERTS 17.0.6.

Identified local baseline: 1,652 operations tests passed/five opt-in native skips/158 known warnings;
`mix precommit` passed 811 tests. Two independent serial native SQL/creation/SCRAM checks passed
with catalog-equal cleanup. These are bounded checks, not exhaustive certification.

Closing-artifact native provision dry-run was planned/changed=false, ten applied/zero pending,
unknown source order requiring downgrade acknowledgment for a future mutating selection.
No closing artifact selection or fresh authentication/email claim applies. An app-created unknown
uploads entry was preserved with a nonblocking warning.

## Private evidence provenance

Recorded protected evidence root: `/tmp/taskman-vps-acceptance.89wn5h8s`.
Closing acceptance bindings are `final-readiness/final-head-binding.json`,
`final-readiness/independent-final-acceptance-summary.json` and
`final-readiness/primary-final-readiness-proof.json`. The `final-readiness/build` directory retains
clean/dirty packaging receipts; `derived-auth-fixed` and `native-restore-resumed` bind authenticated
restore. Locations are provenance recorded at acceptance, not refreshed availability or durable
backup guarantees. Recovery retention belongs to the
[inventory](../inventories/operations-environments.md#private-recovery-and-evidence-locations).

At the closing observation, the native selected application remained the fresh-install artifact
`4ac7b24ce9f7`; its production code equaled the closing source. This does not establish selection of
the closing artifact or acceptance of later changes.

## Accepted limits

- Literal scans establish declared-secret/cookie/surface/recent-evidence coverage, not universal JWT
  or encoded/historical-token forensics. Final scanner SHA256:
  `bbabfa7781a9131e33cd83fc2f3f7e3271e5899d7dea07da93b3ccde7876831a`. Under 120 seconds plus
  five-second kill grace it checked six local and native release/runtime/selected-cookie/process/
  journal categories: 14,941 archive regular entries/366,224,467 bytes, 6,143,927 repository bytes,
  6,599,669 acceptance bytes, selected release 4,981 files/122,078,579 bytes and recent 1,000-line
  journal/102,417 bytes. Four declared secrets and three built cookies traveled in RAM/protected stdin,
  never argv/output; cookie exceptions were its protected file and exact current MainPID `-setcookie`
  argument only. Deployment secrets were forbidden in argv.
- INFO signed-token GET path logging and DEBUG setup[token] filtering remain uncertain; production
  suppresses DEBUG. Coarse controller/security-audit output was checked, not universal framework
  redaction. Private recipients/credentials/signed links/cookies are excluded from public artifacts;
  version-control author metadata was outside the recipient-content correction scope.
- Invalid-CSRF and consumed reset replay were not separately exercised natively.
- Encrypted off-host acceptance transport is not durable disaster recovery or a retention policy.
- Administrator-authenticated restore retains the trusted-backup/RESET ROLE limitation above.
- External workstation IPv6 reachability was not established, despite host-side IPv4/IPv6 HTTPS
  checks. DNS/provider/mail observations are dated information in the environment inventory.
- Accepted loss of combined large-history packaged-consumer coverage and isolated immutable package/
  test boundaries remain in the [parallelism report](2026-09-16-operations-test-parallelism.md).
- Containers/substituted native tools establish only explicit boundaries, not complete VPS/systemd/
  UFW/ACME/mail/reboot/restore acceptance. First failed listener sample and original refreshed capacity
  snapshot were not retained.
