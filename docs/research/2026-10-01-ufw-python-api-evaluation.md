# UFW Python API and host-helper integration evaluation

Evaluated 2026-10-01. Recommendation: use the host's Ubuntu-packaged UFW modules as a
narrow, read-only source of structured saved rules. Retain native `ufw` commands for
mutation. The [selected design](../specs/2026-09-30-additive-ufw-design.md) assumes ordinary UFW
ownership and excludes independent saved-file/kernel auditing; that supersedes the broader audit
recommendation from the initial evaluation. The additive integration is implemented; portable fixture and native-verification requirements
are recorded below. Detailed machine observations and receipts are retained privately.
This evidence is not authorization to change a production firewall.

## Evidence and experiment boundary

Sources: [official upstream source archive distributed by Ubuntu](https://archive.ubuntu.com/ubuntu/pool/main/u/ufw/ufw_0.36.2.orig.tar.gz),
[Ubuntu binary package](https://archive.ubuntu.com/ubuntu/pool/main/u/ufw/ufw_0.36.2-9build1_all.deb),
and [available Ubuntu package revisions](https://archive.ubuntu.com/ubuntu/pool/main/u/ufw/).
The evaluated binary package SHA256 was
`3ece5b2e7a678f5be9678643dc5f8f8457becceec32e863ad1ada1a33e2160b4`.
`README.design` describes alternative frontends using `UFWFrontend`; the backend and
rule objects are shipped implementation interfaces, without a separately versioned API
compatibility guarantee established here. The newer `0.36.2-10` revision visible in the
archive was not evaluated. Do not infer support for every revision sharing `0.36.2`.

The package was extracted into a temporary directory, not installed. An unprivileged
fixture supplied `etc/default/ufw`, `etc/ufw/ufw.conf`, both saved user-rule files and a
synthetic application profile. The real packaged reader ran under Python 3.12.12 and
3.14.7 with `-I -S`. Fixture-only `ufw.common.do_checks=False` allowed unprivileged
initialization. `ufw.util.cmd` admitted only `/usr/sbin/iptables -V`, returning a synthetic
version, and subprocess creation was otherwise blocked. No native firewall command ran.
This establishes reader/import feasibility, not privileged deployment acceptance.

Both interpreters passed assertions for:

- Five ordered IPv4 rules and one IPv6 rule via `get_rules_ipv4()` / `get_rules_ipv6()`.
- A restricted PostgreSQL allow, restricted SSH deny, TCP multiport allow, outbound UDP
  range/interface rule, and SSH limit, with literal source addresses and family preserved.
- Hex-encoded comment decoding through `UFWRule.get_comment()` and saved application-name
  decoding from `Web%20Test` to `Web Test`.
- Structured defaults including input DROP, output ACCEPT, forward DROP and IPv6 enabled.
- Profile-template expansion into TCP `80,443` and UDP `5353` through
  `get_app_rules_from_template()`.
- Constructor/getter execution making only the guarded version call; `caps` remained `None`.
- A raw extra ACCEPT directive and malformed tuple leaving the returned rule count unchanged.
  The malformed tuple produced a warning; the raw directive produced no structured rule.

The last case is material: `_read_rules()` reads UFW tuple metadata, not an authoritative
representation of every packet-filter directive. Metadata can disagree with generated rules.
Malformed tuple warnings cannot count as successful admission. Profile expansion takes a
profile-name template; existing numeric saved rules must retain their actual stored ports,
not be silently reinterpreted using a changed profile definition.

## API boundary

Instantiate `UFWBackendIptables(dryrun=True)` for inspection and use the per-family getters.
Normalize a bounded allowlist of rule fields into our JSON schema; never serialize arbitrary
object state. Defaults/profiles currently expose implementation attributes, so validate their
shape and values explicitly. `is_enabled()` concerns persisted configuration, not proof of
active kernel state. Set up gettext as the normal frontend does and capture warnings separately
from the JSON result. Keep UFW's security checks enabled in production.

Do not call `initcaps()` merely to inspect: its deferred netfilter capability tests can create
kernel test chains. Do not call frontend/backend mutation methods in the read-only bridge.
Native CLI mutation avoids adopting their capability initialization, output, file-writing and
partial-failure contracts. CLI success still requires API/status postchecks and a fresh controller SSH connection.

The API replaces ordinary rule decoding, not authoritative low-level packet-filter validation.
The selected design accepts that limitation under an ordinary-UFW ownership assumption: custom
files/hooks and independent filtering are outside its guarantees rather than comprehensively
checked. Refuse warnings and unsupported exposed shapes; do not claim to detect raw directives
the reader silently omits. Package absence is a prerequisite, never a reason to pip-install a
similarly named wrapper.

## Fit with the host-helper zipapp

The current `helper_client/package.py` builds explicit deterministic archive allowlists and
rejects third-party imports. The transient helper already provides authenticated upload,
root-owned execution, bounded JSON input/output and cleanup. The scheduled-backup archive has
its own smaller allowlist. `host_helper/__main__.py` dispatches a closed operation set including
firewall. The remote runner uses `/usr/bin/python3 -I -S <archive>` for
firewall requests and retains `python3 <archive>` for other operations. No execution mode
supplies a bundled UFW dependency.

The implemented integration permits a narrow stdlib-only exception
for the transient helper's firewall adapter. Load the trusted OS UFW package lazily in process
through a fixed package-specific loader. The selected design changes the existing runner's
firewall invocation specifically to `/usr/bin/python3 -I -S <archive>`; other operation invocation
argv remains unchanged. Do not enable site or add the whole system package directory to `sys.path`. Preserve the existing transport/envelope and archive
allowlists, adding only our operation/adapter. Other operations must work with UFW absent;
the scheduled-backup archive remains stdlib-only.

This removes the separate child, extracted bridge script, internal JSON protocol and bridge
cleanup. The already-protected zipapp supplies the real entry-script path UFW checks. The adapter
implements trusted-origin validation, gettext initialization, bounded byte-compatible warning/output
capture, initialization deadlines and object normalization. Its declared dependency must be visible to
packaging checks rather than concealed through dynamic import. Native CLI mutations remain.

A temporary two-member zipapp (`__main__.py` plus bridge data) passed under both interpreters:
`python -I -S <archive>` started an isolated child, which explicitly loaded the extracted UFW
package and passed the same reader assertions. The package's parent directory was absent from
`sys.path`. This was a packaging prototype, not a change to Taskman's helper or protocol.

An additional temporary zipapp loaded UFW directly in its own process through that same fixed
loader. It passed the reader assertions on Python 3.12.12 and 3.14.7 under `-I -S`, with warnings
captured and a single clean JSON result. UFW's `_print()` writes UTF-8 bytes through the stream's
`buffer`: plain `StringIO` capture raises `TypeError`; `TextIOWrapper` over `BytesIO` admitted
both normal Python text output and UFW bytes in the prototype. Production capture still needs
explicit byte limits and restoration of process-global stream/gettext state. The fixture retained
its unprivileged-check exception and guarded native-version stub; privileged startup and deadline
handling remain implementation verification. No vendored UFW modules, controller virtualenv, pip
dependency, persistent helper or second SSH transport is needed. An explicit UFW exception is
simpler than preserving an absolute stdlib-only host-code boundary through another process.

## Implemented adapter fixture evidence

The production adapter and generated helper have been exercised with the same checksum-verified
Ubuntu `ufw_0.36.2-9build1_all.deb` extraction under CPython 3.12.12 using the repository's
opt-in official-reader and isolated `-I -S` archive tests. The package/interpreter prototype
above separately covers CPython 3.14.7; it is not a privileged supported-image matrix.
The official-reader tests use test-only extracted-path and trust substitution, stub the reader's
privilege-check method, and simulate only `/usr/sbin/iptables -V` and `/usr/sbin/ufw status`.
They retain actual package loading, construction, getters, saved numeric profile ports, comments,
order and real UFW warning emission. No package installation or firewall mutation occurs.
Disabling `common.do_checks` emits `WARN: Checks disabled` and correctly fails production capture;
the fixture instead substitutes only the check method. Production security checks remain enabled.

Reproduce with an operator-selected trusted extracted Ubuntu package root. These checks exercise
the supplied source; they do not authenticate it or require an adjacent archive:

```sh
TASKMAN_TEST_UFW_PACKAGE=/absolute/path/to/ubuntu-package \
  uv run --project ops pytest -o addopts='' -q \
  ops/tests/host_helper/test_ufw_adapter.py ops/tests/host_helper/test_package.py \
  ops/tests/test_integration_packages.py -p no:cacheprovider
```

## Native verification implications

Exercise the actual decorated controller operation and fresh pinned SSH path, not just an inner
adapter function. Preserve rule ordering, each address family, comments/profile associations and
unrelated configuration through convergence and scoped fixture cleanup. Use private recovery and
receipts; actual host inventories and observed machine versions are not public documentation.

Compatible saved policy can become visible after installation. Validate defaults, administrator
identity, SSH safety and ordinary drift before activation. Initialization must retain security
checks even when a test fixture permits unprivileged import. Package removal, kernel effects,
startup and recovery require separately authorized native evidence; container results do not
establish a production network, boot path or full-host restore.

The pyinfra execution-time ContextObject must be tested with an explicit None check: a boolean
fallback can invoke State length semantics before dispatch. Production-path regression tests must
cover this boundary. General acceptance contracts are in the
[native acceptance guide](../guides/native-acceptance.md).
