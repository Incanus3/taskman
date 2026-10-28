# Taskman development guide

This document contains durable project-wide guidance for building Taskman. It is intentionally
separate from product requirements, delivery planning, and session handoffs.

## Delivery approach

- Work top-down through user-visible vertical slices.
- Keep the first implementation narrow and learn from the running application.
- Refine the next slice when it is close to implementation; do not create a detailed backlog for the
  entire MVP in advance.
- Use the product documents as constraints, not as an invitation to expand MVP scope.
- Prefer subagent-driven development for implementation work when it is available and not explicitly
  forbidden.

## Just-in-time architecture

Avoid speculative architecture, but do not skip deliberate design where a real feature needs an
abstraction.

Immediately before a feature or slice, think through the minimum abstractions, boundaries, and
persistence decisions that it genuinely requires. Add abstractions only when a current feature or a
clearly identified boundary justifies them. Keep justified seams small and replaceable where the
product specification identifies a future integration boundary.

Do not design generalized systems for hypothetical future requirements. Conversely, do not avoid a
necessary abstraction merely because a broader version of it is not needed yet: design the smallest
useful seam before adding the feature that needs it.

## Application boundaries

Repository code has no external code consumers, including the operations
package. Server-side executables built and deployed by operations remain
in-scope consumers and must be verified when shared code changes. Unused
internal Python names do not require compatibility shims; this does not waive
documented CLI, protocol, persisted-data, or deployment contracts.

Modules in `TaskmanWeb` interact with persistence through public context APIs. They must not call
`Taskman.Repo` directly or construct Ecto queries. Context and core-library modules own persistence
coordination and keep schemas, changesets, queries, and Repo calls out of the web layer.

Use three levels of ownership inside an application context:

- The main context module exposes application use cases to web, CLI, and other contexts. It owns
  application-boundary validation, authorization entry points, multi-resource orchestration,
  auditing, externally meaningful side effects, and stable application-level results and errors.
  The context namespace owns these use cases, while the main context module may remain a stable
  facade that delegates their implementation to focused workflow and capability modules. It should
  delegate raw queries and mutations.
- Resource and dedicated capability modules expose reusable domain behavior intrinsic to a
  resource or invariant. Prefer Ash actions for resource behavior. A capability module may own
  persistence coordination when the exact query or lock set is inseparable from maintaining its
  invariant, as with administrator authority.
- A resource-specific `Persistence` module owns storage-shaped primitives and deliberate Ash
  bypasses: direct Repo calls, Ecto queries and changesets, row locks, and bulk updates or deletes.
  These functions do not own authorization, auditing, broadcasts, application workflows, or
  user-facing error translation, and they should not start a transaction that must encompass a
  larger workflow.

Keep a semantic resource-level wrapper when it expresses meaningful domain behavior beyond its
storage implementation. Call a persistence function directly from an internal workflow when a
wrapper would merely repeat a storage-shaped operation.

Framework lifecycle adapters such as Ash changes, checks, preparations, and manual actions may own
the action-specific policy, workflow, or saga they exist to implement, including input extraction,
sequencing, hooks, context metadata, and final result or error translation. Do not extract
single-consumer policy merely to make an adapter artificially thin.

These adapters must delegate direct persistence mechanics—queries, row locking, updates, and
deletes—to focused context or core-library modules that own the affected records. Keep the
workflow in the adapter when it is action-specific. Delegate storage-shaped operations directly
to resource-specific persistence modules, and use domain wrappers only when they add meaningful
behavior.

## Current technology direction

### Elixir and Erlang versions

Non-CI development and release builds target Elixir `1.20.4` with Erlang/OTP `29.0.6`.
Local developers may use mise to manage that pair; the release builder installs checksum-pinned
HexPM Ubuntu binaries directly and does not use mise or alter workstation settings.
Alpine CI uses Elixir `1.20.4` / OTP `29.1.2`, including the
[upstream signal-stack correction](../specs/2026-08-10-alpine-elixir-ci-design.md).
Verify release-only runtime behavior separately against the pinned release pair.

### Application architecture

- The implementation is greenfield in `Incanus3/taskman`.
- Use Elixir/OTP with Phoenix LiveView.
- Use Phoenix's default Tailwind-backed component setup.
- Use the conventional Phoenix structure with contexts and separate core and web libraries:
  `lib/taskman/` and `lib/taskman_web/`.
- Use PostgreSQL from the start. PostgreSQL is run manually in Docker during local development.
- Run Phoenix directly with `mix phx.server`; do not add Docker Compose or application container
  orchestration initially.
- Local source development remains a supported workflow. Dedicated-host operation uses the approved
  OTP release behind systemd and loopback Caddy topology; follow the deployment runbook rather than
  adding a second server lifecycle or container layer.
- Prefer a LiveView-first browser experience, adding small isolated JavaScript hooks only when
  browser behavior genuinely requires them. Do not build a JavaScript SPA.

These are high-level technology decisions, not a complete architecture. Decide persistence details,
filesystem boundaries, URL and LiveView state mechanics, ACP process behavior, and testing seams in
context immediately before the relevant feature.

### JavaScript asset dependencies

Use `mix setup` for initial assets or `mix assets.build` after changes. These commands install the
locked npm packages under `assets/` through `npm ci`; asset builds need Node and npm. Commit both
`assets/package.json` and `assets/package-lock.json`; keep `assets/node_modules` ignored.
`mix assets.deploy` installs the same lockfile before minification and digest generation. CI installs
Node/npm, and the existing release builder already contains them. The OTP release serves the
compiled assets.

Vanilla Calendar Pro uses its exact published npm package. Heroicons and daisyUI remain
Mix-managed Git dependencies because their repositories contain directly consumable SVG/plugin
assets. Keep the Calendar MIT notice in the small project-owned re-export wrapper and check it
against the installed license when changing the package pin; do not restore copied library code
or a manual download/update recipe.

## Scope discipline

- Keep Project, List, and Task data in one shared workspace without per-user ownership or
  collaboration permissions.
- Authentication, hosted access, and OTP release packaging follow the accepted hosted-access
  specification. Do not expand that boundary into synchronization, collaboration, managed hosting,
  or speculative multi-user authorization.
- Ash is initially isolated to the Accounts domain. Do not build new domain capabilities as Ash
  resources alongside the existing Ecto contexts. A complete domain migration requires its own
  approved design and coherent migration boundaries.
- Keep external integrations behind focused boundaries when they first appear; do not design the
  final provider architecture before the first concrete provider feature needs it.
- Treat product documents as the source of truth for current behavior. Research and prototypes
  provide evidence and guidance, but do not silently change the product contract.

## Operations development

These rules apply to all future work in the repository's operations tooling, including controller
code, provisioning, host helpers, scheduled tasks, designs, and implementation plans.

### Prefer simplicity within the supported reliability boundary

Prefer the simplest understandable implementation that preserves the desired operator behavior,
data safety, and core reliability guarantees. Reasonable reliability does not require automatic
recovery from every theoretically possible sequence. Do not accumulate state, recovery branches,
abstractions, or repeated checks solely to handle extremely unlikely combinations. State unsupported
cases and a safe refusal/manual-recovery boundary explicitly instead of silently promising recovery.
Changing an already accepted behavior or safety guarantee requires an explicit design decision.

The ops threat model assumes trusted operators and no malicious actor deliberately interfering
with managed operations. Handle unintended clashes: overlapping commands, scheduled jobs,
interrupted processes, ordinary input mistakes, and changes between planning and execution.
Use the lifecycle lock, fresh checks at meaningful consequence boundaries, and native atomic
operations where appropriate. Do not add race/TOCTOU defenses, repeated identity checks, or elaborate
coordination solely to resist intentional concurrent tampering or a compromised administrator.

This does not waive protections against accidental data loss or secret exposure. Preserve scoped
destructive targets, explicit destructive confirmation, backup/reference safety, migration
compatibility, truthful failure reporting, and ordinary path/input/permission/checksum validation.
It does not weaken public application authentication, the network boundary, or SSH verification.
Evaluate each additional mechanism against a concrete supported failure and its cost. Prefer a
small shared capability with actual consumers over a generic workflow or recovery framework.

Treat characterization as evidence of current behavior, not as a decision that every observed
detail must remain permanent. Preserve behavior tied to material guarantees. Reproducing an
incidental refusal, drift classification, change marker, or recovery detail exactly must justify
its complexity when a simpler implementation still protects confidentiality, integrity,
availability, privilege isolation, recoverability, and failure blast radius. Do not add migration
logic, compatibility aliases, or legacy diagnostics for interfaces that were never released or
consumed.

Prefer established library, framework, and native-tool behavior for ordinary convergence when it
is safe, rerunnable, and observable. Bespoke operations require a concrete material risk that the
established capability cannot reasonably control. Keep one authoritative production path for each
capability; do not retain an unused adapter beside a separate live implementation. Tests must
exercise the path production actually invokes rather than treating isolated adapter tests as
production coverage.

Minimize independently configurable and persistent state. Prefer a small set of authoritative
inputs and derive child directories and other dependent values from them instead of exposing each
value separately. Choose between transient and durable mechanisms by comparing their total
lifecycle cost, including installation, versioning, compatibility negotiation, rollback, cleanup,
and metadata. Do not make either lifetime an architectural rule without that comparison.

Give each invariant one clear owner. Across a process or protocol boundary, the caller should
verify that a result matches the request's operation, identifier, and protocol version and contains
the required evidence, but should not duplicate the authoritative side's state discovery or policy.
Share transport, protocol, locking, and evidence mechanics where they are genuinely common while
keeping materially different operation policies explicit. Do not introduce a generic workflow
engine merely to reduce repeated syntax.

Tests should cover distinct state transitions and consequential failure boundaries. Do not multiply
every interruption by every command, flag, and timing permutation when they exercise the same
invariant. Own detailed input combinations in the lowest layer that implements the behavior.
Integration tests cover representative main paths, wiring and distinct cross-boundary failure
consequences; crossing another boundary alone does not justify repeating the lower-level matrix.
Keep focused public-boundary coverage and direct tests for distinct recovery states.
Code-size and duplication measurements are signals to review complexity, not targets that justify
hiding it. Confirm that a simplification removes redundant behavior or state rather than merely
relocating it, and do not trade understandable boundaries or meaningful tests for a smaller count.

### Prefer Python for durable operations workflows

This preference applies to durable repository operations scripts and their implementation, not to
agents' everyday repository work or temporary commands. For inspection, searches, edits and other
agent workflows, choose Bash, standard command-line tools or Python according to the task. Use
Python when it has a concrete advantage, not because of this operations implementation rule.

Use Python for nontrivial durable operations logic: branching workflows, structured parsing,
filesystem state management, subprocess coordination, retries, and recovery. This applies to new
code and substantial changes to existing operations workflows, whether controller-side or host-side.
Host helpers retain their standard-library-only packaging boundary, with one explicit exception:
the transient helper's firewall adapter may lazily load the host's Ubuntu-packaged `ufw` library
in process. Limit the import-validator exception to that adapter, verify trusted system-package
origins, and retain stdlib-only behavior for other operations and the scheduled-backup archive.
Do not vendor UFW or install it through pip. Reuse the existing helper transport.

One-line shell calls, or a few lines where demonstrably simpler, remain appropriate when they
improve clarity or have a concrete advantage. Keep native tools such as `systemctl`, `psql`, and
`pg_dump`; invoke them through bounded argv-based subprocess calls from Python where possible.
Do not replace a native tool with a Python reimplementation, or turn a multi-step shell script into
one long embedded string to satisfy a size check. Explain a substantial-shell exception where it
is introduced.

This is not a prerequisite to rewrite all existing shell code. The
[PostgreSQL host-side Python proposal](../specs/2026-09-09-postgresql-host-python-design.md) remains the
separately scoped refactor of its existing workflow; its parked status does not limit this general
preference for future ops work.

## Verification expectations

Every implementation slice should have a clear user-visible or technical outcome and a small,
meaningful verification gate: focused tests, formatting, a build, a smoke test, or another direct
inspection appropriate to the change.

ExUnit's default `:tmp_dir` may be beneath the checkout. CLI tests using real Git discovery must
use private temporary roots outside the checkout with per-test cleanup, or an explicit
`GIT_CEILING_DIRECTORIES` around the fixture. Prove the intended non-Git or disposable-repository
classification with real Git using the same invocation environment; naming a directory "outside
Git" does not isolate parent discovery. Pass cwd and environment per call rather than changing
process-global state.

Automated tests should cover observable behavior, interactions, and meaningful structure. Do not
add assertions that only verify styling details such as spacing, colors, or alignment. CSS-class
assertions are appropriate only when they establish functional user-visible state, such as whether
an element is shown or hidden.

Use focused LiveView tests for server-owned interactions and rendered outcomes. Keep Wallaby browser
tests to the minimum: add one only when the behavior cannot be verified with a standard LiveView or
lower-layer test, and do not repeat those tests in Wallaby. Name the specific browser behavior the
test must prove. Examples include local storage, browser history, clipboard results, connection
recovery, computed visibility that affects interaction, and functionality that changes with viewport
size. Responsive visual acceptance may use direct browser inspection without adding an automated
browser test. Measure the actual page-context CSS viewport dimensions and horizontal scroll width;
device-emulation labels alone do not establish the tested width. Run Wallaby tests through
`mix test.browser` with Chromium and ChromeDriver; keep
them separate from the normal `mix test` gate.

Browser tests that require headed Chromium need a working display. Use the desktop session's
`DISPLAY` when available. On a headless Linux machine, install Xvfb and its `xvfb-run`/`xauth`
prerequisites, then use a virtual desktop:

```sh
xvfb-run -a -s "-screen 0 1440x900x24" mix test.browser
```

Append a test file or directory to run a focused subset. Choose the browser mode for the behavior
being verified and assert any required capabilities, such as `(hover: hover)`. Switching to
headless mode must not weaken those assertions.

If browser results reflect older JavaScript or styles after switching revisions, check for stale
compressed assets from an earlier `assets.deploy` run. The test endpoint serves existing `.gz`
files, while `assets.build` refreshes only the uncompressed bundles. Regenerate both before retrying:

```sh
MIX_ENV=test mix do assets.build + phx.digest
```

Test the current supported behavior. An absence assertion is appropriate when it is part of that
behavior, such as confirming that a successful operation returned no error. Do not add assertions
that a removed, deprecated, or superseded feature, state, element, field, or column is absent. For
removals, inspect the implementation or migration and test the intended resulting behavior.

Meaningful persisted or query operations exposed through the UI must ship with corresponding API,
CLI, help, Bash/Fish completion, bundled skill, and focused verification parity unless the
accepted feature specification records an explicit exception.

For hosted operation, preserve the public boundary: Caddy owns public HTTPS and Phoenix binds to
loopback. Forwarded client details are trusted only from that immediate loopback proxy. Keep
runtime secrets outside version control, use versioned immutable releases selected by a `current`
symlink, and treat migration compatibility and backup/restore evidence as release requirements.

### CLI verification

Run the focused suite and rebuilt executable gate from the repository root, sequentially:

```sh
mix test test/taskman/cli
mix precommit
mix escript.build
TASKMAN_TEST_ESCRIPT="$PWD/taskman" mix test test/taskman/cli/escript_test.exs
```

Without `TASKMAN_TEST_ESCRIPT`, only the three explicit executable smoke tests are skipped.
The final command must execute all three against the newly built binary. Keep the generated
escript out of version control and use disposable HOME/XDG, Git and skill-installation roots;
verification must not install into real user state. Source-level or Req.Test success does not
establish built-executable parity, live-backend acceptance or the pinned release runtime.

### Operations verification

Run operations-package checks from the repository root:

```sh
uv sync --locked --project ops
uv run --project ops python -m compileall -q ops/taskman_ops ops/tests
uv run --project ops pytest ops/tests -n 4 --dist worksteal --max-worker-restart=0
bash -n ops/taskman ops/caddy/render-caddyfile
mix precommit
```

The operations gate uses four explicit workers; pytest's default remains serial. Run
`uv run --project ops pytest ops/tests` for serial diagnosis. Avoid concurrent source edits or
application builds while running the operations suite: administrator bridge tests share Mix build
output. See the [parallelism measurements](../research/2026-09-16-operations-test-parallelism.md)
for the audit, workstation timings, and fork/thread warning caveats.

For changed command or documentation surfaces, also exercise the relevant
`./ops/taskman COMMAND --help` output, check local Markdown links and whitespace, and check that
current product surfaces contain no leaked planning identifiers. Native systemd asset tests must inspect both
exit status and diagnostics: a successful exit alone does not prove every directive was accepted.

At pyinfra file/directory declaration boundaries, serialize numeric Unix permission bits
as octal-digit strings (for example, `format(mode, "o")` or `"600"`). Pyinfra interprets
integer modes as octal digits, so Python literals such as `0o755` are incorrect inputs.
Keep numeric permission bits for native filesystem APIs and validate declaration tests
against pyinfra's actual mode interpretation.

Sensitive SSH commands intentionally suppress stdout and stderr, including innocuous receipts.
Protected file convergence reports unchanged/changed through exit statuses `0`/`3`, normalizing
actual shell failures to `1`; do not make callers parse suppressed output. Test with suppression
enabled. A failed or interrupted write may already have changed the destination, so retain known
or possible mutation evidence without exposing command output or credential bytes.

For build or packaging changes, run `./ops/taskman build` from a clean, identified checkout and
verify the resulting archive, manifest, checksum, pinned builder identity, and exact-input cache
reuse without connecting to a host. The [runbook](deployment.md) owns artifact handling and the
separately authorized disposable-host acceptance gates. Container and local test results do not
establish real systemd, firewall, ACME, email, reboot, or complete restore acceptance.

Runtime configuration must compile under the pinned release Elixir/OTP, not only the local
development version. Regex `E` export support begins in Elixir 1.19.3; use runtime compilation
with supported options when the same configuration must also load on older pinned releases.
Keep pattern matching unchanged. The isolated build-time release `eval` gate uses synthetic
configuration and external temporary storage; it must not start the application or package its
temporary configuration. See [Elixir Regex options](https://hexdocs.pm/elixir/main/Regex.html#module-modifiers).

Credential-prompt changes require real-terminal verification on the pinned runtime as well as the
local runtime. `StringIO` and a substituted service launcher do not establish native-terminal
compatibility. Run the focused test against an extracted release without starting the application
or using real credentials:

```sh
TASKMAN_TEST_RELEASE=/absolute/path/to/extracted/taskman uv run --project ops pytest ops/tests/test_terminal.py
```

The test runs the prompt modules packaged in that release without starting the application.
Without the variable, it compiles the current prompt sources using local Elixir. Neither mode
establishes native SSH/systemd acceptance. The historical OTP 27 runtime cannot support the
prompt's reversible raw/cooked terminal API; that API was introduced in OTP 28. Use the current
OTP 29 release for credential entry. Do not treat local-runtime success as evidence that the
deployed prompt works.

Architecture checks run within the operations pytest suite. Cross-suite test support lives in
focused modules under `ops/tests/support/`; domain-specific support stays beside its consumers.
Execute generated Python zipapps with `-I -S` in isolation tests: `-I` alone still loads site
packages and can mask missing archive members through an editable workstation installation.
Test non-firewall operations without UFW available. Exercise the firewall adapter's explicitly
loaded OS dependency separately under the same isolation flags, and verify that packaging rejects
third-party imports in every other helper module and in the scheduled-backup archive.
Import shared fixtures explicitly to preserve their intended scope rather than enabling them
globally. Treat code-size counts as diagnostic evidence, not a size target;
the [deployment design](../specs/2026-09-09-dedicated-host-deployment-design.md#simplicity-and-maintenance)
explains why quality, safety, and coherent responsibility take precedence over size.
