# Additive UFW convergence

Status: specification and implementation plan approved on 2026-10-01; local implementation and bounded corrections independently verified; bounded container/staging native evidence accepted by the operator on 2026-10-01.

## Purpose and context

Taskman provisioning must accept compatible additional UFW rules on a host with other services.
It must preserve ordinary UFW-managed rules while adding Taskman's fallback port protection
and checking administrative access. UFW is assumed to own the host firewall. Acceptance is not
permission to replace an existing firewall policy.

This is the additive firewall increment tracked by `tas-lny6.2`. Real host identity, inventory,
evidence and private historical records remain outside readable public repository content. The [inventory privacy policy](../inventories/operations-environments.md)
requires production facts to remain in private operator storage; this specification does not establish current production compatibility.
No production firewall application or Taskman provisioning is authorized.

Repository inspection at `2be9863e` established:

- `host/firewall.py` builds global SSH/HTTP/HTTPS allows and application/database/distribution/EPMD
  denies. Its active-state action and change probe require exact normalized rule equality.
- Its normalization sorts rules and removes IPv6 markers. It therefore cannot establish
  ordering or separate protection of each family.
- Inactive UFW is accepted only without saved rules. Active UFW with compatible extra rules is
  refused rather than converged.
- `host/baseline.py` invokes firewall convergence inside provisioning after other baseline
  operations. Platform admission does not currently inspect the firewall.
- `ops/tests/host/test_firewall.py` covers exact-set acceptance, conflicting/saved-rule refusal,
  activation ordering and a fresh pinned SSH connection.
- Configuration checks distinct configured service ports and reserves TCP 80/443, but does not
  prohibit an SSH/private-port conflict with the fixed EPMD port 4369.
- The checkout is a linked Git worktree. Application dependencies are available; the application
  precommit gate passes. This design changes no implementation code or host settings.

The [dedicated-host design](2026-09-09-dedicated-host-deployment-design.md) and
[operations contracts](2026-09-18-operations-contracts.md) remain authoritative outside the
firewall changes specified here. Shared Caddy/PostgreSQL ownership and SSH-alias transport remain
separate increments. Provisioning remains incompatible with the shared production host until
those other boundaries are resolved.

## Evidence and implications

The [Ubuntu Resolute UFW manual](https://manpages.ubuntu.com/manpages/resolute/man8/ufw.8.html)
documents ordered first-match rules, saved-rule inspection, IPv4/IPv6 behavior and application
profiles. The [UFW framework manual](https://manpages.ubuntu.com/manpages/resolute/man8/ufw-framework.8.html)
describes before/user/after rules files and initialization hooks. The
[Ubuntu firewall guide](https://ubuntu.com/server/docs/firewalls/) documents named service ports,
source-scoped rules and application-profile expansion.

Design inference: a set comparison over `ufw status` cannot prove the desired boundary. Native
UFW rule order and family must be retained, profiles resolved to actual protocols/ports, and
unsupported rules exposed by inspection must cause refusal. The API is not an audit of custom
packet-filter configuration; the supported-configuration assumption below is explicit.

The repository pins pyinfra `3.10.0`. Inspection of that installed package on 2026-10-01 found
an `iptables` operation module but no UFW operation or UFW references in its operations package.
The [official operations catalog](https://docs.pyinfra.com/en/3.x/operations.html) and
[upstream UFW-module discussion](https://github.com/pyinfra-dev/pyinfra/discussions/1559) provide
external context. Direct iptables rule management would bypass UFW's saved-policy ownership.
Keep native UFW execution inside the project's guarded pyinfra operation; native package
installation remains an ordinary pyinfra operation. Reassess a future UFW integration against
the actual preservation, fresh-state and SSH guarantees rather than assuming it supplies them.

### Selected UFW integration

Use the Ubuntu-packaged UFW Python API for read-only saved-rule/default inspection and native
`ufw` commands for changes. The [isolated API and zipapp evaluation](../research/2026-10-01-ufw-python-api-evaluation.md)
confirmed ordered per-family rules, comments, source restrictions and profile associations on
Python 3.12 and 3.14, plus an isolated zipapp/child loading prototype. Sources:
[upstream source archive distributed by Ubuntu](https://archive.ubuntu.com/ubuntu/pool/main/u/ufw/ufw_0.36.2.orig.tar.gz)
and [evaluated Ubuntu package](https://archive.ubuntu.com/ubuntu/pool/main/u/ufw/ufw_0.36.2-9build1_all.deb).
The initial package experiment used extraction, fixture privilege-check substitutions and a
guarded version probe. Subsequent native experiments in the linked research establish privileged
loading, rule preservation and genuine pinned SSH for the exact installed container userspace.
They do not establish a broader supported distro/kernel matrix or production compatibility.

The API reads UFW metadata and can skip malformed tuples or raw directives. Reader warnings,
unsupported exposed rule forms and invalid structured fields cause refusal. Undisclosed raw
rules, hand edits and other filtering are outside the guarantee; there is no independent saved
file/kernel audit. This accepted limitation is the basis for the simpler ownership contract.

Use our adapter through the existing transient host-helper zipapp, loading the host's packaged
UFW code lazily inside the existing helper process under a narrow stdlib-only exception. Do not
vendor UFW or install a pip wrapper. The
[research report](../research/2026-10-01-ufw-python-api-evaluation.md) records stale independent
wrapper activity and the absence of an established separately versioned Python API guarantee.

## Agreed requirements and trade-offs

1. Preserve unrelated existing user rules, comments, relative order and application profiles.
   Never reset, delete, replace, prepend or reorder them to obtain acceptance.
2. Add missing required Taskman rules through native UFW commands, without recreating existing
   equivalent rules. A compatible complete active policy is a no-op.
3. Accept and preserve existing source-address-restricted rules, including permits to private
   ports and denies/rejects/limits on public ports. This includes PostgreSQL permits. They remain
   operator-owned exceptions with their existing precedence.
4. Refuse all-source permits overlapping private ports and all-source rules that may block
   required SSH/HTTP/HTTPS access instead of overriding them. Source-specific exceptions do not
   remove the fresh SSH verification requirement.
5. Preserve compatible defaults. Refuse conflicting defaults instead of silently changing them.
6. Validate both address families separately and verify fresh strict pinned SSH after mutation.
7. Support ordinary UFW-managed host rules and application-profile rules under the assumption
   that UFW owns the firewall and its generated files are maintained through UFW. Refuse unsupported
   forms encountered through the API. Hand-edited files, custom hooks and independent firewall
   policy are outside the guarantee and are not comprehensively detected.

These choices provide additive management of ordinary UFW, not certification of the entire
host firewall. The operator is responsible for the supported-configuration assumption. State it
in the deployment guide and material preview; do not add a separate policy flag or pretend that
an inspection pass proves that assumption. Known conflicts still require operator resolution.

## Required protection

Let `public_ports` be `{ssh_port, 80, 443}` and `private_ports` be
`{application_port, database_port, distribution_port, 4369}`. Ports are numeric TCP destinations.
Duplicate private ports are deduplicated. Configuration validation must reject intersection
between these sets before SSH or package installation; in particular `ssh_port=4369` is invalid.
No new firewall flags or environment policy knobs are introduced.

For IPv4 and IPv6 independently, require incoming TCP allow coverage from any source to any
local destination for each public port, and incoming TCP deny coverage from any source to any
local destination for each private port. These are fallback requirements: earlier accepted
source-specific rules may allow private access or block/limit public access for their sources.
Preserve the current all-source SSH fallback: a narrower
existing SSH allow alone is insufficient and convergence adds the missing all-source rule.
An accepted earlier source-specific SSH deny/reject/limit retains its existing effect. This does
not introduce a policy that restricts SSH solely to the sources of existing allow rules.

Private-port protection here concerns new incoming TCP connections. Ordinary UFW loopback and
established/related handling are assumed intact. Accepted source-specific private-port permits are
intentional exceptions, so acceptance does not mean those ports are unreachable from every
remote source. UDP services, unrelated existing connections,
application authentication and provider firewalls are outside this firewall contract.

## Supported rule model and refusal policy

The read-only adapter normalizes the API's ordered per-family rules with direction, action,
protocol, source/destination networks, source/destination port sets, interface restrictions,
logging and comments. Use numeric
ports and normalized `ipaddress` networks internally. Do not evaluate saved command strings as
shell code. Comments have no policy effect and never enter executed argv.

Supported user rules:

- Host `in` and `out` rules with `allow`, `deny`, `reject` or `limit` actions; omitted direction
  means `in`.
- TCP, UDP or omitted protocol, numeric single ports, inclusive ranges and comma-separated sets;
  omitted destination port means all ports. Resolve named ports using the host's service database
  for the relevant protocol; unresolved or ambiguous names cause refusal.
- Literal IPv4/IPv6 addresses and CIDRs, or `any`; no hostname resolution or negated predicates.
- Optional single interface, source-port restriction, logging and comment fields.
- Application-profile rules use their saved numeric protocol/port expansion as reported by the
  API, preserving profile associations. Do not reinterpret existing rules using a changed profile
  definition. Invalid or unresolved saved expansion causes refusal. Profile files are never rewritten.

Unknown tokens, incomplete/malformed rules, routing rules, unsupported protocols and unresolvable
predicates cause refusal. An unsupported exposed rule cannot be ignored merely because its
profile name
appears unrelated. Reader warnings are failures; silent raw-rule omissions remain outside the
supported-configuration guarantee. Do not write a second decoder for generated directives.

Classify a rule as source-address-restricted when its literal source IP/CIDR is a proper subset
of that rule's address family. `any`, `0.0.0.0/0`, `::/0`, and equivalent normalized whole-family
networks are all-source. An interface, destination or source-port restriction alone does not
make a rule source-address-restricted. Compare source coverage within each family, not against
the combined IPv4/IPv6 universe.

Evaluate overlap conservatively, without a generic packet simulation engine:

- Preserve and accept every supported source-address-restricted rule even when its action/port
  overlaps a Taskman requirement. Do not add a preceding rule that would override it. Do not
  combine several source-specific rules into an all-source refusal; their combined coverage is
  operator-owned. Keep them in the material preview so their exceptions remain visible.
- An all-source incoming `allow` or `limit` that includes TCP and overlaps a private destination
  port is a conflict, irrespective of earlier denies, destination address, source port or
  interface. An omitted protocol includes TCP. An omitted destination port includes every port.
- An all-source incoming `deny`, `reject` or `limit` that includes TCP and overlaps a public port
  is a conflict, irrespective of earlier allows. An all-source rate-limited SSH rule is refused;
  a source-address-restricted SSH limit is accepted and preserved.
- All other supported host rules are retained. An outgoing rule does not satisfy an incoming
  requirement; outgoing rules may still make application operation fail and are not certified by
  firewall acceptance.

Examples: an unrelated `allow 10050/tcp` or expanded Samba profile is compatible when its ports do
not overlap the configured protected ports. `allow from 192.0.2.0/24 to any port 5432 proto tcp`
is accepted when the database port is 5432; a missing global database deny is appended after it.
`deny from 192.0.2.0/24 to any port 443 proto tcp` is likewise retained before an appended global
HTTPS allow. An all-source `allow 3900:4100/tcp` conflicts with application port 4000. A
source-restricted version is accepted. An all-source TCP permit without a destination port
conflicts with all private ports; a source-restricted version is an accepted broad exception.

An existing public allow satisfies a requirement only when it covers all sources/destinations,
has no source-port or interface restriction, and contains that TCP destination port. An equivalent
port range, multiport rule or profile can satisfy it. Private deny coverage follows the same
unrestricted match requirement. Keep rule families distinct; an IPv4 rule cannot satisfy IPv6.
Rules wider than the required single port remain unchanged when they otherwise pass policy.
Source-specific rules cannot substitute for a missing global fallback. Preexisting global rules
may already shadow later source-specific rules; preserve that order rather than promising to
activate an exception that was previously ineffective.

## Administrator SSH safety

Source-specific rule compatibility does not authorize locking out the deployment controller.
The operator retained this separate safety gate. Obtain source and destination addresses from
the current authenticated remote connection's preserved `SSH_CONNECTION` value in the helper,
before initializing UFW; validate its four fields and require its destination
port to match configured SSH. Do not read SSH
configuration or infer the source from the workstation's local address behind NAT. Missing or
invalid connection evidence is preflight failure before firewall mutation.

Evaluate the ordered incoming TCP rules for that source/destination and SSH port, including the
prospective appended fallback. Use a small administrator-path check, not a general packet-policy
engine. The source port of a future connection is unknown: source-port/interface predicates that
could match are treated as possible matches. A definitely matching allow with no such uncertain
predicate establishes the path; continue past definitely nonmatching rules. If a deny, reject or
limit can be reached before that proven allow, refuse convergence with a controller-path reason.
An earlier definitely matching allow can prove that a later blocking rule is shadowed. Treat
`limit` conservatively because it does not guarantee a fresh connection under current load.
An allow with an uncertain predicate alone is insufficient evidence; evaluate the later path too.

Thus a source-specific SSH deny for another source is accepted, while one covering the controller
can make the host unsafe to converge even though the rule itself is supported and preserved.
Never move or replace either rule to pass this gate. The check is required for active as well as
inactive convergence. Refusal retains the rule and requires operator resolution of the controller
access path. After mutation the real fresh pinned connection remains the consequential check.

## Defaults and supported-configuration boundary

Require persisted default incoming policy `DROP` (UFW `deny`), outgoing `ACCEPT` (UFW `allow`)
and forward `DROP`/`REJECT` (non-permitting routed default). Default incoming `reject`, outgoing
deny/reject, or permitting forwarding is refused. Never issue a `ufw default` command to change
an existing default. Require `IPV6=yes`, even without public IPv6 DNS; do not silently enable it.

Read activation status from bounded `LC_ALL=C ufw status` output, recognizing only its explicit
active/inactive header; unexpected or missing status causes refusal. The API's persisted enabled
flag is not proof of runtime activation. Validate persisted settings through the API; do not
claim to independently establish the kernel's defaults or rule correspondence.

UFW must own the host firewall, with ordinary UFW-managed generated files and no custom hooks
or independent packet-filter policy affecting these guarantees. Do not compare package file
hashes, decode generated directives, reconcile `iptables-save`/`ip6tables-save`, inspect native
nftables rules, or install `nftables` for this increment. Custom filtering can defeat the API's
model and fresh-SSH prediction; the real reconnect check covers connectivity only, not private
port reachability. There is no claim to detect every unsupported arrangement.

Inactive UFW with compatible saved rules is supported. Enabling it makes those rules effective;
the preview must state that consequence. Absent UFW requires package installation through the
existing native package operation, followed by default/API inspection before activation. Its
absence does not prove the absence of another firewall; the ownership assumption still applies.

## Inspection, preview and convergence

Use one firewall capability in the existing transient host-helper zipapp. Its stdlib code owns
normalization, policy, delta calculation, native command execution and postchecks. Add a closed
`firewall` operation to the existing `HostRequest`/`HostResult` protocol; do not create another
remote transport, standalone rendered Python payload, persistent helper or configuration store.
The scheduled-backup archive and existing operation behavior remain unchanged.

Use an in-process adapter, with an explicit `ufw` dependency exception limited to that adapter
in the transient archive's import validator. Other helper modules and the scheduled-backup
archive retain their stdlib-only boundary. Load UFW only when handling the firewall operation;
other operations must work without it installed. No separate child, extracted adapter script,
internal JSON exchange or bridge cleanup is needed.

For the `firewall` operation, the existing runner executes the protected zipapp with fixed
`/usr/bin/python3 -I -S <archive>` through its current sudo/SSH path. Other operations retain
their current invocation; this is not a second transport. Production isolation must match the
firewall package tests. Load `/usr/lib/python3/dist-packages/ufw/__init__.py` using a small fixed
package-specific `importlib` loader, without adding all dist-packages to `sys.path` or enabling
site. Verify trusted package/module origins and root-owned path components not writable by
non-root users. Do not accept caller-supplied modules, code or paths. Do not use pip or vendor
UFW. Declare the exception in packaging rather than concealing imports from the AST guard.
Keep UFW security checks enabled: the entry script is the existing protected zipapp, a real
root-owned path. Initialize gettext in the adapter and capture warnings/diagnostics without
polluting helper JSON output. UFW's output routine writes UTF-8 bytes via a stream's `buffer`;
plain `StringIO` is insufficient. Use bounded byte-compatible capture and restore stream/gettext
state afterward. Any reader warning causes refusal. Normalize objects into a bounded model. Use
`UFWBackendIptables(dryrun=True)` with per-family getters; do not call `initcaps()` or mutation
methods. Native UFW mutation may perform its normal capability probes.

Use bounded native argv, `LC_ALL=C`, captured diagnostics and a 45-second firewall-operation
deadline, including API initialization and its native version probe. Keep the existing helper
invocation/output limits as outer bounds. Do not print raw captured diagnostics publicly.

Derive administrator connection evidence from the authenticated invocation's preserved
`SSH_CONNECTION`, validating the fields and SSH port as above. Inspection returns that identity;
convergence checks it again. The existing runner already preserves this variable through sudo.

Use the existing protocol version, correlation and envelope limits. For `firewall` requests:

- `paths` is empty; `parameters` has exactly `mode` (`inspect` or `converge`) and `ports`
  (exactly `ssh`, `application`, `database`, `distribution`, each an integer 1..65535 excluding
  booleans). EPMD 4369 is fixed, not caller-configurable.
- `expected_state` has exactly `snapshot_sha256`: null for inspection, or a 64-character lowercase
  hexadecimal digest for convergence. Reject other keys and mismatched mode/digest pairs.
- Results use the existing outcomes. `succeeded` means ready inspection or verified convergence;
  `refused` means invalid input, policy conflict or unsupported exposed state; `retryable` means
  a read-only prerequisite or failure before possible mutation; `manual` means failed work with
  known/possible mutation. Helper process exit follows the existing envelope contract, not a new
  firewall-specific exit protocol. Controller exit categories are described below.
- `state` has exactly `reason`, `changed`, `possibly_changed`, `snapshot_sha256`,
  `administrator_connection`, `exceptions`, `proposed`, and `completed`. Flags are booleans;
  identity is validated source/destination literal IPs or null when unavailable. `exceptions`
  lists normalized accepted source-specific exceptions on owned ports for preview; command lists
  contain bounded native argv, never executable user text. `completed` includes only successful
  mutations. A failed/prerequisite inspection has null snapshot and empty lists. Successful
  inspection always has false mutation flags and empty `completed`. Failed convergence uses a
  null snapshot and retains known completed commands and mutation flags.
- Reasons are `compatible`, `invalid_input`, `policy_conflict`, `unsupported_state`,
  `inspection_failed`, `execution_failed`, `postcheck_failed`, `ufw_missing`, and
  `controller_path_blocked`. `ufw_missing` is a prerequisite only in inspect mode, with false flags.
  Validate request/result identity, schemas and outcome/flag consistency on both sides. A bad or
  missing result after convergence dispatch conservatively means possible mutation.

Validate firewall evidence separately from release/database mutation schemas. Do not enroll
firewall work in release lifecycle locks or broaden the parked provisioning-admission workstream.
Permit up to 4096 rules per family in the adapter and up to 4096 entries specifically in
`firewall` result exceptions, without raising unrelated protocol collection limits. Native/adapter
output and the outer result remain bounded by 1 MiB; overflow fails instead of truncating evidence.

Inspection is read-only and computes the same policy/delta that convergence uses. Integrate it
into provisioning preflight before confirmation and baseline mutation when UFW is present.
Include the ordinary-UFW ownership assumption, required additions, accepted source-specific
exceptions on owned ports, activation consequence, and unchanged defaults in the existing material
preview, including `--dry-run`. If UFW is absent, preview package installation and the required
fallback rules that may already be covered by saved policy. State that installation can reveal
compatible saved rules which will be preserved and activated, with the actual missing subset
applied after helper validation. Installed-package validation remains a pre-activation gate. An installed UFW
package whose API cannot load or be inspected is a failure, not permission to mutate or install
a replacement pip wrapper.

The snapshot digest covers activation state, compatible defaults, per-family ordered normalized
user rules including comments and saved profile associations/expansion, plus the administrator
source/destination identity. Exclude counters and ephemeral source ports.
Include the validated administrator source/destination identity in the material preview; refresh
it with the policy before mutation and refuse if it changed. Ephemeral source ports are not frozen.
Respect the operation/protocol limits above; limit overrun is failure, never truncated acceptance.

Before mutation, refresh actual firewall state and compare the snapshot and delta to the
confirmed preview. A changed policy causes refusal and a new invocation, not silent scope
expansion. Counter changes alone are immaterial. An absent-UFW preview is the explicit exception:
after approved installation, accept any policy the existing helper classifies as compatible,
including saved unrelated rules and supported source-address exceptions, with unchanged
administrator source/destination identity. Preserve that policy and activate it if inactive;
apply only the actual missing fallbacks. The initial preview explicitly authorizes this compatible
saved-policy discovery/activation, so differing rule digests, exceptions or reduced additions do
not alone require a new preview in this installation case. Conflicts, unreadable state, missing
package or changed administrator identity still refuse. This exception does not weaken the normal
installed-policy material drift comparison. Bind the actual inspected non-null digest before
convergence; do not compare it against an invented empty snapshot.
Execute the same policy again at the native convergence boundary; do not trust cached admission.
The convergence helper checks `expected_state.snapshot_sha256` against its initial inspection before
mutation. For initially absent UFW, the controller obtains and validates the compatible
post-install snapshot and binds that digest before dispatch. Null is invalid in convergence mode.
This does not implement the parked whole-provisioning lock/admission design.

Convergence sequence:

1. Inspect complete supported state and calculate missing rule coverage. Refuse contradictions
   and an unproven administrator SSH path before issuing firewall mutations.
2. Append only missing per-family rules using fixed numeric argv
   `ufw allow from SOURCE to any port PORT proto tcp` for SSH, 80 and 443 in that order, then
   `ufw deny from SOURCE to any port PORT proto tcp` for application, database, distribution and
   EPMD, deduplicated in that order. For each port, consider IPv4 then IPv6; `SOURCE` is exactly
   `0.0.0.0/0` or `::/0` for that family. Skip a covered family completely. Never insert or
   prepend. Native UFW applies the explicitly selected family; confirm resulting coverage in
   both. Both-family duplicate append commands can overwrite existing comments when repairing
   only one family and are therefore excluded. At most fourteen additions plus activation are
   admitted; command arguments contain no caller-supplied source, comment or profile text.
3. If inactive, enable only after all required coverage is verified in saved rules:
   `ufw --force enable`. If already active, do not disable/enable or issue an unnecessary reload.
4. Inspect again. Require active status, unchanged compatible defaults, complete per-family
   coverage and preserved relative order/semantics/comments of all preexisting user rules.
5. After any successful firewall mutation, require a new strict pinned-host-key controller SSH
   connection to execute `true`, closing it afterward. Do not reuse the existing session.

A complete active policy yields no mutating command and `changed=false`; it does not need a
fresh post-mutation SSH connection. Inactive compatible saved rules still require enablement
and the fresh connection. Never treat a failed partial action as converged.

## Failure and recovery contract

Use existing controller categories: invalid port/input status `2`, inspection/transport/native
execution/fresh-SSH failure status `5`, policy or unsupported-state refusal status `10`.
Reports use stage `firewall`, bounded reason codes and an actionable next step. The helper
returns a validated `HostResult` and suppresses captured native diagnostics. A transport failure
or invalid helper result is not a successful inspection.

Before any mutating command is dispatched, firewall-local `changed=false` and
`possibly_changed=false`. After any successful addition/enablement, `changed=true`. A failing or
interrupted mutating command may have applied its change: retain `possibly_changed=true`, and
project either known or possible mutation conservatively to the existing `OpsError.changed`.
Fresh SSH failure after changes must retain that evidence. Existing pyinfra evidence aggregation
must also preserve earlier unrelated provisioning changes; firewall-local refusal does not claim
the whole invocation was read-only.

After failure, stop further provisioning. Do not reset UFW, remove additions, disable protection,
restore a whole policy snapshot or attempt automatic reconnect-and-mutate recovery. Compatible
partial resources are retained. A reachable host can be inspected and rerun through the same
validation. Lost SSH requires operator console recovery and pinned identity verification before
retrying. No console access or automatic rollback capability is assumed.

## File responsibilities and scope

| File or owner | Responsibility |
| --- | --- |
| `ops/taskman_ops/host/firewall.py` | Controller plan, helper invocation, pyinfra action and fresh SSH/error translation |
| New `ops/taskman_ops/host_helper/operations/firewall.py` | Firewall policy/delta, native execution and operation result |
| New `ops/taskman_ops/host_helper/ufw_adapter.py` | Lazy in-process OS UFW loading, read-only object normalization and diagnostics handling; sole UFW import exception |
| `ops/taskman_ops/host_protocol/` | Closed firewall request/result validation and operation-specific bounds |
| `ops/taskman_ops/host_helper/__main__.py` and helper package builder | Dispatch, explicit archive membership and module-specific UFW exception; preserved backup boundary |
| `ops/taskman_ops/helper_client/runner.py` | Use fixed system Python with `-I -S` for firewall requests; retain existing transport and other operation invocations |
| `ops/taskman_ops/config.py` | Validate public/private port-set intersection including EPMD |
| `ops/taskman_ops/host/baseline.py` | Retain firewall ordering; pass approved inputs and retain UFW package installation |
| `ops/taskman_ops/workflows/provision.py` and `provisioning.py` | Read-only firewall preflight/preview and material-state refresh; preserve error/change aggregation |
| `ops/tests/host/test_firewall.py` and focused helper tests | Policy, real adapter/helper execution, preservation, failure and idempotence |
| Provision/config/architecture tests | Public workflow integration, invalid input, stdlib/transient helper and scoped OS dependency boundary |
| `docs/guides/deployment.md` | Supported rule forms, defaults, refusals, inactive activation and manual recovery procedure after implementation |

Keep the helper implementation as one focused firewall capability; do not create a generic
policy/workflow framework. Existing internal shell-rendering interfaces need no compatibility shim: repository
code is their only consumer. Production uses the same runtime that tests exercise.
The application release, database services, Caddy ownership, global SSH configuration, Beads
planning identifiers and private host facts do not enter implementation-facing surfaces.

## Verification and acceptance

Focused automated coverage must establish:

- Clean inactive installation, inactive compatible saved rules, active partial policy, complete
  compatible active policy and repeated convergence with no new rule/activation.
- Preserved unrelated single/range/multiport, source-scoped, interface-scoped, logging/comment and
  application-profile rules; equivalent coverage avoids duplicates.
- Accepted source-specific private permits (including PostgreSQL), public denies/rejects/limits,
  broad/range/profile rules and both families; retained precedence after global fallbacks are
  appended. All-source equivalents must refuse, including explicit `/0` sources.
- Source-specific SSH exceptions accepted for other sources; a reachable deny/reject/limit on
  the controller path refuses before mutation, while a proven earlier allow can establish safe
  shadowing. Unknown future source-port/interface matches fail closed. Validate connection
  evidence from SSH rather than local-address guesses; preserve source exceptions on refusal.
- All-source private TCP conflicts with broad permits, omitted protocol, ranges/profiles and
  `limit`; all-source public blocking/limit conflicts. UDP-only unrelated rules remain compatible.
  Refusal issues no firewall mutations.
- Distinct IPv4/IPv6 evidence, missing-family repair, `IPV6=no` refusal, configuration EPMD conflict
  and invalid/ambiguous saved ports/profile expansions.
- Compatible defaults remain unchanged; unsupported defaults/exposed routing or malformed API
  fields, reader warnings and truncated/oversized inspection output cause refusal. Do not assert
  detection of hidden custom files/hooks or independent filtering.
- Preflight/dry-run performs no mutation; material change after preview refuses before firewall
  mutation; actual convergence revalidates its inputs.
- SSH allow precedes inactive enablement; a fresh pinned connection is required after additions
  or enablement. Native failure, interrupted mutation, invalid helper result and post-change SSH failure
  retain truthful evidence and stop the workflow.

Use executable native-command fakes for consequential failures, packaged UFW reader fixtures and
real zipapp subprocess tests, not shell-string assertions alone. Verify isolated in-process
import origins, byte-compatible warning capture and initialization deadlines, privileged read-only
initialization, absent/mismatched package handling and unchanged scheduled-backup
archive. Verify non-firewall operations without UFW installed and reject UFW imports outside
the adapter or in the backup archive. Verify firewall production argv selects system Python
with `-I -S`, while other invocation argv remains unchanged. Verify operation request/result
validation and mutation
evidence survive invalid results.
Also validate a disposable Ubuntu 26.04 environment with real
UFW/application profiles and IPv4/IPv6 rule files. Use privileged isolation only with separately
authorized disposable infrastructure; do not run live firewall tests on the workstation or
production host. Containers can establish privileged loading, native rule convergence and the
actually exercised genuine fresh pinned SSH path with independent recovery access. They share
the workstation kernel and do not establish Ubuntu kernel/boot or production-network acceptance.
The operator accepted the combined container/staging scope: real Ubuntu staging outer-operation
execution, both-family preservation, fresh pinned SSH and cleanup, complemented by container
installation/activation and controlled partial-failure evidence. Reboot, deliberate SSH-loss/console
recovery and global IPv6 remain untested accepted limits, not claims of complete host acceptance.

After implementation, run focused tests and the repository operations gate from
[the development guide](../guides/development.md#operations-verification), plus `mix precommit`.
Check changed CLI help, Markdown links, whitespace and implementation terminology. An unavailable
real-host gate must be reported as pending; simulated checks cannot substitute for it.

For this documentation checkpoint, verify self-containment, every local link, no placeholders or
contradictions, preserved handoff commitments, and no code/host mutation. The application precommit
baseline passes; it does not establish acceptance of the proposed firewall implementation or
real-host behavior. Current verification evidence belongs in the workstream handoff.

## Rejected alternatives and next steps

Validation-only adoption leaves required additions to manual operations and does not deliver the
requested idempotent convergence. Separate Taskman chains add installation, ordering and lifecycle
ownership that this increment does not need. Permissive subset matching of status output hides
ordering/family evidence. Independent generated-file/kernel/nftables auditing adds disproportionate
complexity for the selected ordinary-UFW guarantee and is deliberately excluded. Automatically
deleting conflicts or changing defaults violates
preservation and operator control. A generic netfilter evaluator exceeds the supported boundary.

A Taskman application profile would introduce a generated file/update lifecycle while public
proxy ports, host SSH and private Taskman ports still have different owners and allow/deny
semantics. The operator selected explicit per-port rules for this increment; existing unrelated
application profiles remain supported and preserved.

Local implementation, independent verification and operator acceptance of the bounded native scope
are complete. The [native acceptance guide](../guides/native-acceptance.md) preserves portable
criteria and limits; actual host evidence remains in private operator records. Future host changes
and publication require separate authorization.
