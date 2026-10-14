---
name: operating-taskman
description: Use when an agent needs Taskman operations CLI guidance for provisioning, deploying, verifying, backup or recovery, including shared PostgreSQL access/setup and proxy-pending exit 13 on shared Caddy.
---

# Operating Taskman

Use the repository controller `./ops/taskman` for host operations. This reference is separate from
the application `taskman` CLI for Projects, Lists and Tasks. Read it from the checkout; it is not
automatically installed and adds no management command or host-action authority.

The [deployment runbook](../../docs/guides/deployment.md) owns procedures. Read the relevant
section before proposing or performing an operation; obtain explicit operator authorization for
host changes. Repository checks, builds and this skill never grant deployment permission.

| Need | Canonical reference |
| --- | --- |
| SSH host-key enrollment, missing entry or pin mismatch | [Operator shell and known-host prerequisites](../../docs/guides/deployment.md#operator-shell-environment) |
| Target, artifact and ordinary confirmation | [Preview](../../docs/guides/deployment.md#preview-before-changing-a-host) and [build identity](../../docs/guides/deployment.md#build-an-artifact) |
| Shared PostgreSQL, failed SCRAM access or deliberate configuration override | [Ownership/access](../../docs/guides/deployment.md#postgresql-ownership-and-access), [operator setup/retry](../../docs/guides/deployment.md#postgresql-operator-setup-and-retry) and [override/recovery](../../docs/guides/deployment.md#explicit-postgresql-configuration-override-and-recovery) |
| Shared Caddy, bootstrap or preserved configuration | [Caddy ownership](../../docs/guides/deployment.md#caddy-ownership-and-provisioning-outcomes) |
| Exit 13 / `application-provisioned-proxy-pending` | [Activation and exact-target retry](../../docs/guides/deployment.md#activate-a-pending-taskman-site) |
| Validation/reload failure or lost reply | [Site recovery and runtime uncertainty](../../docs/guides/deployment.md#caddy-site-recovery-and-runtime-uncertainty) |
| Other failure, backup, rollback or restore | [Statuses and rerun decisions](../../docs/guides/deployment.md#exit-statuses-and-rerun-decisions) and the matching command section |

Read `./ops/taskman COMMAND --help` for current flags. `--yes` confirms the displayed provision/
deploy plan only; downgrade acknowledgment and migration policy remain separate. `--json` is
never confirmation. Dry-run performs discovery without managed host mutation and may build locally.

Existing PostgreSQL provisioning owns only Taskman's configured role/database/private credentials.
Preserve native HBA/settings, PostgreSQL packages/service state and other clusters by default,
including reruns after bootstrap. `postgres_package_track` selects a PostgreSQL major version;
missing or ambiguous targets refuse rather than installing/upgrading or choosing another cluster.
Application queries require actual SCRAM proof with a version-16-or-later native client. Missing
resources show unproven access until creation; compatible role-only retry preserves its password.

For `5 / postgresql` access/client failure or `10 / postgresql` safety refusal, read the reported
endpoint, selected HBA, failed check and known/possible retained facts. Check first-match HBA
ordering, `pg_hba_file_rules` parser results and independently protected credentials; broader HBA
coverage may suffice. Operator changes and selected-cluster reload/restart need their own
approval. Rerun the report's exact `./ops/taskman provision ENV` command only after setup and
ordinary confirmation. Failure stops before preparation/migrations/history and is never exit 13.

`--force-postgresql-config` is provision-only, deliberate whole selected native HBA/settings
convergence with a possible selected-cluster restart interrupting all consumers. `--yes` never implies it. Preview first and
arrange other consumers/recovery; the flag bypasses no identity, loopback, resource or provenance
safety check and performs no existing-cluster package upgrade or adopted-password rotation.
Actual changes retain private two-file originals plus metadata under
`/etc/taskman/postgresql-config-recovery/<random-uuid>/`; no-op creates none. Keep reported copies
on success/failure. Native HBA rollback is scoped to HBA, not whole settings/runtime recovery.
Restoration, service actions and manual cleanup each need separate authorization.

For pending activation, retrieve the runbook's procedure: coordinate a quiet window including
watchers/reloaders, reconcile duplicate hostname blocks, add the top-level known import once when
missing, validate as Caddy, then reload an active unmasked service or deliberately unmask/start/
enable an inactive one. Main configuration and other sites remain operator-owned. Existing Caddy
provisioning owns only `/etc/caddy/sites-enabled/taskman.caddy`.

Exit 13 is schema-1 `status: "failed"` with explicit pending facts: application setup passed on
the deployment host, but public verification and completion remain pending. Actual failures keep
their own statuses. Pending publishes no new successful selection, retains recovery protections,
and may leave scheduled backups unavailable before first success. It does not authorize `deploy`;
`verify` alone cannot finalize history. Existing completed replay history stays intact.

For example, after separately authorized operator activation, preview and confirm the retained
exact target with its adjacent manifest/checksum:

```sh
./ops/taskman provision production --artifact /secure/artifacts/taskman-RELEASE.tar.gz --dry-run
./ops/taskman provision production --artifact /secure/artifacts/taskman-RELEASE.tar.gz
```

An unchanged source/build identity is another exact retry route; a changed checkout is a new target.
Full public verification/history publication gates completion; then verify backup operation and
use `deploy` for later releases. Restoring the site on disk never proves restored Caddy runtime
after an uncertain reload. Preserve evidence and inspect before another approved action.
