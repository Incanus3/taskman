# Native operations acceptance

Local tests establish implementation behavior; they do not establish native systemd, firewall,
proxy, database, mail or recovery outcomes. Use a separately authorized disposable environment
for native acceptance. This guide describes portable acceptance contracts and findings. Real
machine inventories, execution packets, credentials, exact receipts and recovery locations stay
in private operator storage outside version control.

## Scope and authority

Approve the exact scenarios, target, maintenance window, allowed changes and recovery strategy
before execution. Refresh console access, service identity, source/artifact bindings and usable
protected recovery. Do not treat a prior approval, unused time, stopped test, successful local
check or historical receipt as authority to repeat a host operation.

Execute one native command at a time. If a command returns a session identifier, wait for that
exact session to finish, inspect its exit and retained receipt, then issue a dependent command.
Stop on unexpected failure or unknown mutation; determine actual state before selecting recovery.
Never weaken a failed check or silently change a frozen execution packet during a run.

Use finite individual command, observation and cleanup limits. Any overall outage or recovery
limit must have a concrete recovery purpose and operator agreement. Include evidence persistence,
process exit, connection closure and coordination when proving bounded completion; an internal
end timestamp alone does not establish whole-command completion.

## Preservation and recovery

Capture fresh protected on-host and off-host recovery before disruptive tests. Bind exact target
identity and native files, application/data/credentials, service state and backup references.
Preserve unrelated resources. Unknown or incomplete capture/quarantine state requires explicit
resolution; it cannot authorize another recovery branch or an automatic retry.

Compare only changes the scenario actually authorizes. A newly adopted empty target may acquire
its intended migrations, while its identity and every unrelated resource remain protected.
Record deliberate authentication repairs separately, then compare the retry against the proven
post-repair baseline and verify credential/verifier continuity across failure and success.

Full row fingerprints are evidence for explicitly scoped disruptive staging recovery, not a
routine deployment requirement. Match their cost to data size and disclose structural-only or
reduced coverage. Readable backup catalogs prove readability, not a completed data restore.

Restore the original application, authentication, services and backup scheduler before accepting
results. Verify stopped integrity before guarded startup, then native/public health and an
actual authenticated browser. Select no-event versus persistent catch-up scheduling against fresh
calendar/state proof; record normal retention and protected copies of any pruned originals.
A failed or uncertain activation does not authorize a second enable or manual backup/pruning.

## Shared Caddy scenarios

Exercise preservation of the operator's main configuration, sibling sites, package/principal
identity and private data. Cover compatible import recognition, missing-import pending behavior,
initial preparation, activation completion and healthy unchanged reruns. For new installations,
verify package/bootstrap masks and protected configuration recovery with actual native tools.

Exercise validation rejection, reload failure, lost reply and restoration failure separately.
Restore only the exact owned site; preserve directories, sibling files and mutation evidence.
A restored file and an active service do not prove which configuration is loaded after an
uncertain reload. Package recovery is separate from site rollback.

Prove actual routing, forwarding and connected browser behavior. Distinguish reconnect after
reload from transport behavior after restart. Preseeded certificates do not prove new issuance;
post-install checkpoints do not prove interruption during package-manager writes.

## Shared PostgreSQL scenarios

Exercise compatible resource adoption, isolated SCRAM authentication, selected track/port,
ambiguous selection, explicit forced convergence and desired-state force no-op. Cover private
capture refusal and failures before writes, after settings, after HBA, at restart refusal and
at lost reply. Bind each case to fresh native identity, credentials and complete recovery.

Default provisioning preserves shared HBA/settings and other clusters. A selected-cluster forced
restart can interrupt sibling consumers; prove this with an admitted synthetic backend that
actually remains active until restart, its lost connection, backend disappearance and changed
postmaster identity. Cancellation during recovery is cleanup, not interruption evidence.

Prove the actual SCRAM exchange; an HBA method name alone is insufficient. Correct psql file/stdin
variable semantics and check every native listing/query status. Validate retained credential-file
bytes before any probe uses them, retaining safe-mode, owner and provenance checks.

Restore captured native configuration through the already admitted recovery branch. Establish
whether recovery metadata is absent or matches the exact case baseline before invoking a qualified
restore. Never switch branches after failed or unknown restoration. Finish original recovery
before constructing a new case with corrected synthetic dependencies.

## Portable observation findings

- Package paths can have supported aliases under merged `/usr`; require resolved-file equality
  and package authority rather than a guessed literal path.
- Kernel pseudo-files can report zero stat size while returning data. Use a fixed-path bounded
  reader for boot identity; keep ordinary protected-file length checks unchanged. Journal boot
  selectors require their documented UUID format.
- Locale, SQL address formatting and missing initialization can break an observer without proving
  a server failure. Preserve the first receipt, diagnose through bounded read-only checks and
  review a corrected observer without weakening its assertions.
- Restrictive umasks require explicit fixture modes. Synthetic fixtures must satisfy the same
  ownership, no-clobber, credential and safety contracts as production inputs.
- Volatile capacity counters and narrowly approved metadata effects are not blanket preservation
  exemptions. Record exact allowed deltas; never rewrite timestamps to manufacture equality.

The [deployment runbook](deployment.md), [operations contracts](../specs/2026-09-18-operations-contracts.md),
[Caddy design](../specs/2026-10-01-shared-caddy-design.md) and
[PostgreSQL design](../specs/2026-10-07-shared-postgresql-design.md) own production behavior.
Private acceptance results do not authorize deployment, disposal, push, merge or publication.
