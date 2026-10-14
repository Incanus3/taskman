# PostgreSQL provisioning with operator-owned cluster configuration

Status: approved design; local implementation, scoped review, local verification and bounded native acceptance are complete. Selected-cluster ownership, native access proof,
compatible adoption, reporting and explicit configuration override with retained private backups
and operator-controlled recovery are accepted. Native and external-action authority remain separate.
Workstream: `tas-lny6.4`, under `tas-lny6`. Design evidence baseline: `1f18ba74` on `operations/shared-host`.

## Purpose and decision inventory

By default, provision Taskman's own database resources on an existing PostgreSQL cluster without
replacing authentication, changing cluster settings, or interrupting other consumers. If the operator's
configuration cannot support the required connection, return a failure with actionable setup
context. Keep new dedicated-host installation supported and allow deliberate configuration
convergence through a targeted override with a visible blast radius.

| ID | Status | Decision | Downside or constraint | Contract |
| --- | --- | --- | --- | --- |
| D1 | Accepted | Sole-consumer status applies to the selected cluster; other clusters stay outside Taskman scope | A selected cluster still needs reliable identity proof | [Select one cluster](#select-one-cluster) |
| D2 | Accepted | Taskman owns isolated resources; operator owns shared configuration; unsuitable access requests operator setup | Provisioning cannot automatically repair shared authentication or listeners | [Installation ownership](#installation-ownership), [Operator setup context](#operator-setup-context) |
| D4 | Accepted | Retain configured PostgreSQL major-version selection and refuse ambiguous or missing targets among existing clusters | Multiple clusters within one track need operator resolution | [Select one cluster](#select-one-cluster) |
| D5 | Accepted | Prove application access using native SCRAM-required connections, without an HBA interpreter | Access can fail after new Taskman resources are created; requires a capable libpq client | [Prove actual access](#prove-actual-access) |
| D6 | Accepted | Adopt compatible partial resources without password rotation; retain ownership and data-provenance checks | Incompatible resources require operator repair rather than automatic normalization | [Resource adoption and interrupted setup](#resource-adoption-and-interrupted-setup) |
| D7 | Accepted | Extend existing provision authority and preview; retain existing commands and numeric categories | Internal projections and their consumers must change together | [Preview and authority](#preview-and-authority), [Failure results](#failure-results) |
| D10 | Accepted | `--force-postgresql-config` explicitly authorizes selected existing-cluster configuration convergence; every existing installation otherwise stays resource-only | Replacing HBA/settings or restarting can break other consumers; operator owns the decision | [Explicit configuration override](#explicit-configuration-override) |
| D8 | Rejected | Per-invocation Taskman-only declaration plus broad corroborating checks | Substantial catalog/coordination logic still cannot independently exclude dormant consumers | [Why there is no sole-consumer detector](#why-there-is-no-sole-consumer-detector) |
| D3 | Superseded | Allow existing global convergence when Taskman is proven sole consumer | Reliable proof is too complex; strict absence-only D9 was considered before explicit override D10 | [Why there is no sole-consumer detector](#why-there-is-no-sole-consumer-detector) |
| D9 | Superseded | Allow global convergence only for initially absent PostgreSQL | Too restrictive for deliberate operator repair; D10 retains the safe default and adds explicit authority | [Explicit configuration override](#explicit-configuration-override) |

D1 was explicitly selected during resume. D2 was explicitly selected with the requirement that
PostgreSQL behave like shared Caddy: manage isolated parts and provide a clear failure when shared
configuration is unsuitable. D10 was explicitly agreed after rejecting complex sole-consumer
proof in favor of an operator-controlled override. D4 selection and D5 native SCRAM proof with retained resources after failure are accepted.
D6 compatible resource adoption, D7 preview/failure results and concrete D10 recovery mechanics
are accepted. The implementation plan separately authorizes local implementation. D3/D9 preserve superseded alternatives and D8 preserves the rejected proof approach.

## Evidence and inherited constraints

A supported shared installation can have unrelated consumers, multiple registered cluster tracks
and working operator-owned loopback authentication. Provisioning must preserve that configuration
and prove the selected endpoint rather than assume a dedicated server. Actual machine inventories,
role/database names and credentials are private deployment inputs; the
[inventory privacy policy](../inventories/operations-environments.md) governs their storage.

At the source baseline:

- `services/postgresql.py` builds desired packages, replaces the native HBA, configures
  `listen_addresses`, `port` and `password_encryption`, and can restart the selected cluster.
  `provisioning.py` declares these operations before role/database convergence. This is unsafe
  unchanged on the shared production cluster.
- Resource convergence already refuses mismatched role attributes/memberships and database
  ownership, preserves adopted passwords, and creates missing resources through native tools.
- `host_helper/operations/discover.py` selects by package track and validates live cluster identity,
  but its role/database observation rejects compatible role-only partial state. Host discovery and
  the verification prerequisite also contain admin queries using the default PostgreSQL endpoint.
- Existing listener verification accepts both loopback families. It does not require rewriting
  `localhost` into a single literal address. Actual listener ownership and selected endpoint proof
  remain required.

Retain [operations contracts](2026-09-18-operations-contracts.md), particularly initial-install
admission, migration provenance, protected credentials, truthful mutation evidence, lifecycle
locking and bounded subprocesses. Retain the [simplicity policy](../guides/development.md#prefer-simplicity-within-the-supported-reliability-boundary)
and shared-Caddy completion semantics. Database failure must occur before application preparation,
migration or first-success publication; Caddy's proxy-pending exit 13 is not a PostgreSQL result.

Official PostgreSQL contracts used by this design:

- [HBA ordering and reload](https://www.postgresql.org/docs/18/auth-pg-hba-conf.html): the first
  matching rule decides authentication; failed authentication does not fall through. Includes are
  interpreted by PostgreSQL. Consequently, offering an appended line is not sufficient repair advice.
- [Required authentication](https://www.postgresql.org/docs/18/libpq-connect.html#LIBPQ-CONNECT-REQUIRE-AUTH)
  and [libpq environment variables](https://www.postgresql.org/docs/18/libpq-envars.html):
  `require_auth=scram-sha-256` requires a completed SCRAM exchange; `PGREQUIREAUTH` supplies it.
  [PostgreSQL 16 also documents this capability](https://www.postgresql.org/docs/16/libpq-connect.html#LIBPQ-CONNECT-REQUIRE-AUTH).
- [Password storage and method negotiation](https://www.postgresql.org/docs/18/auth-password.html):
  password creation uses the session's `password_encryption`; an HBA `md5` rule can negotiate SCRAM
  when the stored verifier is SCRAM. The design requires the actual exchange, not a literal HBA method.
- [psql password handling](https://www.postgresql.org/docs/18/sql-alterrole.html): `\password` avoids
  placing plaintext passwords in SQL command history or server logs.

These sources establish native behavior, not current-source integration or Taskman credential
access on production. Implementation and native acceptance must establish those separately.

## Observable behavior

| Situation | Provisioning behavior |
| --- | --- |
| Existing selected cluster, unrelated resources, working loopback SCRAM | Preserve all cluster settings/HBA and other resources; create or adopt Taskman's resources; prove access; continue existing provisioning |
| Existing Taskman-only cluster, no override | Resource-only ownership, even if current catalogs look dedicated |
| Existing cluster with `--force-postgresql-config` | Explicitly preview HBA/settings replacement and possible restart; converge only the selected cluster after confirmation and recovery capture |
| Override combined with unknown identity, foreign port ownership or contradictory Taskman resources | Refuse; the flag grants configuration authority, not a safety-check bypass |
| Existing selected cluster is down, on a different port, or exposes the configured port publicly | Refuse before host convergence; explain required running endpoint/topology; do not start or reconfigure it |
| Several candidate clusters, or other clusters exist but no requested-track candidate | Refuse selection; do not pick the first cluster or install another cluster automatically |
| PostgreSQL genuinely absent | Use the retained dedicated bootstrap; establish one selected cluster, then create/adopt resources and prove access |
| Taskman role/database missing, but shared HBA denies the eventual application connection | Retain newly created resources and private credentials; return failure with mutation evidence and operator setup context; do not migrate |
| Compatible role exists and database is missing | Preserve role/password, create the missing database, and prove access |
| Role is privileged/has memberships, or database has a different owner | Refuse resource adoption; preserve existing resources and credentials |
| Correct credentials supplied to a `trust` or non-SCRAM endpoint | SCRAM-required probe fails; do not accept a successful unauthenticated query as proof |
| Operator repairs shared access, then reruns `provision` | Rediscover and reconfirm; adopt compatible retained resources without password rotation; continue only after a fresh successful probe |

## Installation ownership

**D2 and D10 accepted.** By default, for an existing cluster manage only the configured Taskman role,
database and `/etc/taskman/pgpass` under their existing safety contracts. Runtime environment,
releases and scheduler retain their existing owners. Do not stage an HBA replacement, write an
HBA include or managed block, change global `password_encryption`, change listeners/port, modify
other roles/databases, or start/enable/reload/restart PostgreSQL. Preserve shared configuration
contents and metadata; package upgrades/installations are also excluded from this branch.

This applies on every existing-cluster invocation, including installations originally bootstrapped
by Taskman and clusters that currently appear Taskman-only. Existing compatible configuration
needs no manual action. Incompatible configuration receives the failure/setup context below.
Only the explicit [configuration override](#explicit-configuration-override) grants existing global
write authority; package installation history is not an ownership mechanism.

For genuine absence, retain the existing dedicated package installation and guarded native
configuration path. Require no registered clusters, no contradictory PostgreSQL service/data/config
authority, and no foreign listener at the intended endpoint. Missing inspection tools alone do not
prove absence. Existing PostgreSQL artifacts with no safely selected cluster request operator setup;
do not introduce automatic cluster creation, repair, version upgrades or deletion for those states.

Bootstrap can configure the new cluster's loopback settings and native HBA through the existing
validated path. A failure after installation retains actual partial state and mutation evidence.
A rerun observes an existing cluster and therefore uses resource-only ownership; if bootstrap left
its configuration unsuitable, the operator completes setup manually or deliberately uses the
configuration override after reviewing its preview. No bootstrap provenance marker grants later
authority. Other clusters are never changed.

## Explicit configuration override

**D10 accepted, including the recovery mechanics below.** Add the boolean
`--force-postgresql-config` only to `provision`; its invocation field is `force_postgresql_config`,
default false. It explicitly permits the existing dedicated configuration capability to converge
Taskman's native HBA and the selected cluster's `listen_addresses`, `port`, `password_encryption`
and native HBA-path setting. All existing identity/input/port/permission/native-validation checks
remain. It does not install/upgrade packages on an existing installation, choose another cluster,
rotate adopted passwords, alter foreign roles/databases, bypass migration provenance, reset data,
relax loopback safety, or override an unavailable/ambiguous observation.

With the flag, desired configuration is converged even if existing broader authentication already
permits Taskman. It is not a conditional fallback secretly enabled by a failed probe. Native
change detection avoids writes/restart when the target configuration is already exact. Otherwise,
the existing native path may restart that selected cluster; explicitly warn about interruption of
all its consumers and removal of authentication coverage for non-Taskman connections. Other
clusters remain outside authority. The flag is not a claim that the cluster is dedicated.

Present the selected version/name/data directory/port, each desired setting, the exact replacement
HBA policy, preserved resource scope, potential restart and recovery location before confirmation.
`--yes` confirms the displayed plan but never implies the flag. `--dry-run` previews the requested
override with no writes or proof of eventual success. Reject the flag on `deploy` and other commands
through ordinary argument validation. No environment ownership flag or package-installation marker
is added. On genuinely absent PostgreSQL, bootstrap is already authorized by its confirmed plan;
the flag grants no wider scope.

Before modifying an existing cluster under the override, retain protected recovery copies of only
the two native files the configuration capability can modify: its validated `postgresql.conf` and
native HBA, including original owner/group/mode and identity/digest. Use
`/etc/taskman/postgresql-config-recovery/<random-uuid>/` for each actual configuration change, under a root-owned non-link `0700` recovery parent, with `0700` run directories and `0600`
files `postgresql.conf`, `pg_hba.conf` and `metadata.json`. Metadata identifies the selected cluster,
original native paths, original uid/gid/mode and both SHA-256 digests. Keep copies on success and
failure and report their location; no automatic pruning or recovery-file overwrite. Do not create
a recovery run directory for an exact no-op. Retire copies only through separately authorized
operator cleanup after their recovery need is resolved. Retained private backups and manual recovery
are preferred over no retained copies, which reduces recoverability, or automatic whole-configuration
rollback, which adds restoration/state handling and another potentially disruptive restart.
Use the existing native safe-file and failure boundaries, not a generic snapshot/restore manager.
Do not export role/password catalogs or table data. Native configuration copies may themselves
contain operator secrets; retain them privately and suppress their contents in diagnostics.

Do not claim that the current native HBA failure rollback restores all cluster settings or runtime:
the existing path backs up/restores HBA only. Retain that scoped behavior and truthful failure
reporting; keep the additional original configuration copy available for deliberate operator
recovery. Restoring configuration and restarting/reloading remain separate consequential actions.
Do not automatically restore unrelated files or repair other consumers. Default shared provisioning
performs none of this write/recovery capture because it has no global configuration authority.

## Why there is no sole-consumer detector

**D3/D9 superseded by D10; D8 rejected.** Reliable sole-consumer proof would need to distinguish
standard roles/databases from dormant application state, inspect objects in administrative/template
databases, check replication and jobs, bind a declaration to the selected cluster, and recheck
before restart. Even those checks could only disprove sole-consumer status: an external inactive
job using built-in or Taskman credentials need not leave distinguishable catalog state. Connection
counts are weaker still. An explicit operator assertion would remain essential.

The operator rejected disproportionate verification machinery and selected a specific force flag.
The final approach therefore grants authority explicitly rather than pretending to establish sole
use. A package-origin marker would add persisted state without proving present ownership; default
resource-only behavior instead applies to every existing installation. Automatic global repair is
less convenient, but deliberate repair remains available with a visible blast radius. Reconsider
unattended inferred ownership only with an independently accepted consumer-inventory contract.

## Select one cluster

**D1 and D4 accepted.** Keep existing environment inputs `postgres_package_track`,
`database_host` (`127.0.0.1` or `::1`), `database_port`, `database_role` and `database_name`.
Do not add cluster aliases, ownership profiles, a sole-consumer declaration flag or a durable mode flag.
The D10 configuration override supplies mutation authority after selection; it does not select a cluster.

Inspect registered Ubuntu clusters, applying the optional package-track filter, and require one
candidate. Bind its version, name, data directory, configured/live port and trusted native process
identity. Require administrator access as `postgres` through `/var/run/postgresql` at the configured
port; never query whichever cluster happens to use the default port. Existing selected clusters
must already be online at the requested port. Bind the database listener to that cluster and require
all listeners on the configured database port to be loopback-only, preserving dedicated-host safety.

Different-track clusters do not invalidate a uniquely selected existing candidate and are excluded
from mutation. No selected candidate while other clusters exist is an operator-setup refusal, not
an absent-installation bootstrap. With no track supplied, multiple clusters refuse. On the observed
production topology, track `18` would select `18/main`; this does not choose or publish production
environment values. An exact version/name input would cover more complex same-track hosts but
adds configuration and selection behavior unnecessary for the observed deployment; reconsider only
when such a host needs support.

## Prove actual access

**D5 accepted.** Use native connection proof instead of parsing or reimplementing HBA matching.
Inspect known prerequisites before convergence: selected live endpoint, administrator access,
listener safety, resource compatibility and a client capable of `require_auth`. A client without
that capability refuses; never silently omit the requirement. Unsupported-client diagnosis must
identify the prerequisite separately from a failed application connection. On genuinely absent
bootstrap, check this capability after client-package installation and before resource creation.

For existing resources, default shared provisioning proves application authentication during
admission as well as after convergence. The explicit configuration branch instead uses selected-cluster
administrator catalog/schema observations before configuration and reports application access as
unproven. It defers only application authentication until immediately after configuration/resource
convergence and credential installation; no preparation or migration can cross that proof boundary.
Retain protected credential-file ownership/mode and exact-byte authority checks, role/database
compatibility, empty-schema or migration-provenance proof, and all other admission checks before
confirmation. The flag cannot turn an unknown catalog/schema state into admission authority.
This exception is necessary because an HBA repair must not be blocked by the application connection
it is intended to repair; it does not permit credential rotation or a generic validation bypass.

Carry selected-track and override context through every provision-mode discovery, target resolution,
deployment admission, downgrade-authority observation, credential preflight and post-confirmation
refresh. All use the same privileged selected-cluster observation rather than independently selecting
a default endpoint. Existing target resolution occurs before capability preflight, so changing only
resource convergence or the later `provision_authority` call would leave the override unreachable.

For a new role, prepend `SET LOCAL password_encryption = 'scram-sha-256';` within the existing
transaction before `\password`. Keep secret input on suppressed sensitive stdin. This confines
password creation policy to Taskman's session and does not change cluster defaults. Preserve an
existing role's password/verifier; a legacy verifier requiring rotation is operator work.

After role/database convergence and protected credential installation, connect using explicit
host, port, role and database with `PGPASSFILE=/etc/taskman/pgpass`,
an explicit libpq `require_auth=scram-sha-256` connection parameter, `--no-psqlrc`,
`--no-password` and `ON_ERROR_STOP=1`. Use a non-secret conninfo value such as
`--dbname "dbname=taskman_prod require_auth=scram-sha-256"` with the validated database
identifier and separate explicit host/port/user flags. Unsupported clients reject that parameter;
do not rely only on an environment variable an older client could ignore.
Run a bounded read-only identity query proving `current_user`, `current_database()`,
`inet_server_addr()` and `inet_server_port()` match the request. Require successful native status
and the exact validated response. Suppress native diagnostics; do not expose password material or
parse localized error strings to infer the cause. Use the existing remote/host subprocess bounds
and secret-safe environment handling; do not use `PGPASSWORD`.

When admission has supplied credentials but no retained protected passfile, replace the current
`PGPASSWORD` preflight with a bounded root-only transient passfile inside the existing private helper
workspace. Use `PGPASSFILE` and remove the temporary file on success, failure and timeout through
existing cleanup boundaries. When the retained file exists, validate its metadata and exact bytes
before using it. Deferred configuration admission does not perform this application probe or need
its temporary passfile; it still checks any retained credential authority. Secrets remain suppressed
stdin/file material, never argv, diagnostic output or password environment variables.

Apply the same authentication requirement to existing shared application-credential database
observations used by provisioning and subsequent lifecycle operations. Administrator catalog
queries remain explicit socket/port peer-authenticated queries. Runtime Phoenix access remains
verified through existing application readiness; the libpq option is not a Postgrex configuration
setting or proof that every future connection will use unchanged authentication.

Real proof can only occur after missing resources exist. Avoid a temporary probe role/database,
synthetic password, negative-password matrix or HBA evaluator solely to fail earlier. The accepted
cost is that a refused application connection can leave valid new Taskman resources behind.
Bootstrap and shared tests must distinguish SCRAM success from `trust`, plaintext, legacy MD5,
denied access, wrong credentials and unsupported native clients.

## Resource adoption and interrupted setup

**D6 accepted; failure retention accepted under D5.** Retain the existing least-authority role attributes: LOGIN, INHERIT,
NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOREPLICATION, NOBYPASSRLS and no explicit parent memberships.
The configured database must have that role as owner. Do not rotate passwords, normalize
privileges, drop/recreate databases or change unrelated grants to make an existing resource fit.
Configured names plus validated state and the displayed confirmation supply adoption authority;
no new creation-provenance marker is required.

Support both resources missing, compatible role-only, and compatible role/database. Database
without the expected role, contradictory attributes/memberships/owner, and unknown inspection
refuse. Extend the preconvergence observer to recognize compatible role-only state as an incomplete
planned resource delta rather than declaring the database ready. Preserve empty-database proof and
populated-database migration provenance; ownership alone does not authorize adopting foreign data.

A compatible precreated Taskman database does not by itself require an existing Taskman OS account.
Permit the missing account as a planned baseline creation only after the selected role/database
compatibility and empty-schema or accepted migration-provenance evidence are established. Feed that
evidence into host acceptance before its current existing-database/missing-account refusal. Matching
names or owner alone are insufficient; other filesystem, service and account admission constraints
remain. This explicitly extends the current cross-service partial-state rule rather than silently
claiming all compatible database adoption already works.

Known contradictions refuse before protected credential writes. Missing resources may be created
only after confirmation. If later connection verification fails, preserve created resources,
credential files and earlier provisioning changes, and stop before release preparation/migration.
Do not undo earlier Caddy/systemd changes or drop a role/database automatically. Retry uses ordinary
`provision` discovery and confirmation. The configuration override does not bypass resource
adoption or data-safety checks; no PostgreSQL pending-completion mode is added.
Transport loss retains possible mutation, not a fabricated unchanged result.

## Preview and authority

**D7 accepted.** Add PostgreSQL ownership/access context to the existing redacted `provision`
preview and structured failure result. Show selected cluster or bootstrap intent, endpoint,
role/database creation versus adoption, configuration ownership and explicit override state,
the required SCRAM probe and its potentially post-creation failure boundary. Dry run remains `0/planned`; it cannot claim
application credential proof for resources it did not create.

Extend the existing `provision_authority` inspection/projection with a closed PostgreSQL observation
and build a frozen ownership plan for `ProvisioningInputs`. The privileged observation owns live
cluster/resource discovery; the controller validates request binding and required fields rather
than rediscovering the same state. Keep discovery and execution branches consistent.

The provision-mode ordinary discovery request and `provision_authority` request both carry the
configured `postgres_package_track` and required boolean `force_postgresql_config`; other discovery
modes retain their own closed request schemas. Do not coerce strings or missing booleans into
authority. The existing successful projection adds a closed `postgresql` object with exactly
`branch`, `cluster`, `role`, `database` and `access`:

- `branch`: `bootstrap`, `existing` or `configure-existing`, derived from proven absence/presence
  and the explicit request flag; no caller-supplied branch bypasses inspection.
- `cluster`: null only for bootstrap, otherwise a closed object with `version`, `name`,
  `data_directory`, `config_file`, `hba_file` and integer `port`. Validate native identity and safe
  paths before returning them. Process/listener checks remain internal observation evidence;
  physical target drift must invalidate the frozen plan.
- `role` and `database`: each `absent` or `compatible`; contradictory/unknown resources return
  a failed operation rather than successful authority with another state. Compatible role-only
  state has `role=compatible`, `database=absent`; never invent ready database/schema facts.

- `access`: `unproven` or `ready`; `ready` requires the native application SCRAM/identity proof.
  Bootstrap, missing resources and deferred configuration admission report `unproven`. Catalog/schema
  readiness alone cannot produce `ready` or an overall healthy application-access claim.

Top-level database/schema authority still describes inspected resources and provenance; it must not
be interpreted as application connectivity. Human and JSON previews expose the separate access
state, and action consumers require the successful post-convergence proof before preparation or
migration. The sensitive credential-preflight request uses two closed authentication modes,
`authenticate` and `defer-for-configuration`; the latter is derived only from validated override and
selected-cluster context, never an unconstrained skip-validation input. Owned resource observations, relevant credentials and initial
schema/provenance evidence must be consistent with the existing authority snapshot. Update both
closed producers/validators and all consumers together; no generic extra-key acceptance or legacy
shim is required for these internal, repository-owned consumers.

Revalidate the selected identity, ownership branch and relevant role/database authority after
confirmation and before their consequence. Refuse unexplained target/resource drift and replan.
Do not require global HBA/config byte equality when the operation has no authority to write those
files. The explicit configuration branch instead retains native-file drift/safety checks and
the recovery capture described under D10. The fresh native access proof handles ordinary operator
configuration changes. Preserve the existing lifecycle lock and supported operator-coordination
boundary; no global PostgreSQL lock, configuration fingerprint registry or perpetual monitor is
introduced. Explicit global convergence needs a coordinated configuration/maintenance window,
not an inferred absence of other consumers.

## Failure results

Keep `OpsError` and existing numeric categories:

| Condition | Status / stage | Required result |
| --- | --- | --- |
| Invalid environment | `2`, existing input stage | Existing validation failure |
| Missing/ambiguous selected target, unsafe listener, contradictory role/database, unsafe recovery capture or confirmation drift | `10 / postgresql` | Name the refused boundary and operator setup/replan action |
| Administrator/native inspection unavailable, unsupported client, or actual application connection proof fails | `5 / postgresql` | Name the failed check and required access; preserve uncertainty about its cause |
| Bootstrap package/native convergence fails | Existing classified category; never success/pending | Retain actual known/possible changes and existing installation recovery directions |

The result must retain aggregate mutation state and prior successful provisioning evidence through
dispatch, transport failure and output encoding. `changed=false` is valid only if no earlier or
attempted persistent action warrants changed/unknown evidence. Database access failure cannot
publish a successful selection, migration proof or proxy-pending result. Do not add another exit
code or make all database failures `10` merely because operator intervention is required.

## Operator setup context

Human and JSON output must provide the configured endpoint, selected version/name when proven,
role/database names, the failed check, the observed native HBA path when available, preserved
resource/mutation facts, and the exact retry command `./ops/taskman provision ENV` with the actual
validated environment name. Never expose credential bytes, SQL password statements, native
stderr, raw configuration contents or unproven diagnoses.

For a failed access probe, say that the required SCRAM-authenticated connection could not be
established and that either access configuration or existing credentials may need correction.
Offer this target-specific HBA example for operator review, replacing all values from validated
configuration:

```text
host    taskman_prod    taskman    127.0.0.1/32    scram-sha-256
```

The names above are examples, not approved production values. For `::1`, use `::1/128`. Explain
that the operator must place effective coverage before conflicting matching rules, inspect
`pg_hba_file_rules` for parser errors, and reload only the selected cluster after their own changes.
Existing broader rules can already suffice; do not instruct the operator to add a duplicate blindly.
Listener/port changes can require a restart; default shared provisioning does not perform it.
The explicit override retains the selected-cluster restart behavior described under D10.
Password mismatch/verifier repair is distinct from HBA setup; recheck independent protected
credentials rather than changing an adopted role automatically. Mention `--force-postgresql-config`
as an explicit alternative only with its full-config/restart warning; never recommend it as a
generic way to overcome an unknown failure. The operator must decide that replacing the selected
cluster configuration is appropriate and arrange other consumers/recovery first.

Example human failure with newly created resources:

```text
PostgreSQL access verification failed (exit 5).
Required: SCRAM connection to 127.0.0.1:5432 as taskman, database taskman_prod.
Selected cluster: 18/main. Authentication configuration: /etc/postgresql/18/main/pg_hba.conf.
Taskman role/database and protected credentials were created and retained; no migration ran.
Check effective HBA ordering and the configured credentials. Shared configuration was preserved.
After operator setup, rerun: ./ops/taskman provision production
```

Only report the created/retained and no-migration facts when established; lost replies must instead
state uncertainty. This is a clear failed operation with recovery directions, not a successful
installation awaiting proxy activation.

## Implementation boundaries and verification

Expected source owners:

- `services/postgresql.py`: frozen desired/ownership plan, existing/absent/explicit-configuration
  branches, protected override recovery capture, resource convergence, session-local password
  creation and native application access proof.
- `host_helper/operations/discover.py` and focused shared helper capability if needed: selected
  cluster/resource observations and compatible partial-resource admission. Keep substantial new
  workflow logic in Python without expanding the parked whole-module Python refactor.
- `host/facts.py`, `host/acceptance.py`, `provisioning.py`, `workflows/provision.py`: consistent
  PostgreSQL admission, compatible database/missing-account admission, configured endpoint queries,
  plan binding, preview and failure propagation. Include `workflows/deploy.py` and shared discovery
  request builders because provision target resolution uses their admission path.
- `host_helper/operations/preflight.py`, credential helpers and `helper_client/runner.py`: closed
  deferred-authentication contract, preserved file authority and transient-passfile cleanup.
- `host_helper/database.py`, relevant verification prerequisite and closed protocol validators:
  common authenticated application queries and correctly selected admin endpoint.
- `cli.py`: provision-only boolean flag parsing, invocation transport and help. The operations CLI
  currently has no Bash/Fish completion artifact; application CLI completions are a separate surface.
- Existing PostgreSQL/discovery/provision/verification tests, runbook, CLI help and
  `priv/taskman_ops_skill/SKILL.md`: behavior and command-surface parity.

Verify distinct consequential cases in focused tests before the full gates: untouched shared
configuration/packages/service and unrelated resources; selected 18 plus retained 16; absent versus
missing-target/partial installation; compatible role-only retry and validated precreated database with missing OS account;
wrong owner/privilege/provenance refusal; override reaching preview through all admission paths
with failed application authentication, retained credential authority and explicit unproven access;
transient credential-passfile cleanup on success/failure/timeout;
session-local SCRAM with a different cluster default; successful SCRAM and refused weaker/denied
connections; correct identity/port and unsupported client; existing Taskman-only clusters staying
resource-only by default; explicit override scope, recovery bytes/metadata, no-op, failed convergence
and selected-cluster restart; flag/yes/dry-run/argument-validation parity and target drift refusal;
known/possible mutation retention and human/JSON setup context; no migration/first-success
publication after failure. Reuse existing provenance, resource fixtures and meaningful lifecycle coverage rather than duplicating every case.

Run the repository operations, compile/shell and `mix precommit` gates after implementation.
Native acceptance must use a separately approved disposable-host scope with a sibling database,
preservation/health evidence, operator-setup/retry proof, and a separately authorized explicit
override/no-op/failure-recovery case on a disposable cluster. The ordinary shared case must not
use the override to make its preservation checks pass. Native commands must establish actual
authentication, package/service behavior and preservation; mocks alone do not establish these.
No whole-database row fingerprinting is required for ordinary provisioning verification. Production
inspection here is read-only evidence, not a native provisioning acceptance test.

## Implementation outcome and authorization

The written design decisions and configuration recovery trade-offs are accepted and implemented.
Local verification and bounded native acceptance are complete; actual host results remain in private
operator records. The [native acceptance guide](../guides/native-acceptance.md) owns portable
verification criteria and limits.

Future provisioning, native tests, shared PostgreSQL restart/reload, fixture deletion, pushing,
merging and publication require separate authorization. Accepted results do not establish safety
or authentication on a different target or renew authority for another host action.
