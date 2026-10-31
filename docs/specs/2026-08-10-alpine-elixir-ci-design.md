# Alpine Elixir CI

## Runtime decision

| ID | Status | Decision | Downside or constraint | Rationale |
|---|---|---|---|---|
| D1 | Accepted | Use Elixir 1.20.4 / OTP 29.1.2 / Alpine 3.24.3 | Requires hosted x64 verification after runtime changes | [Released correction](#context) |
| D3 | Accepted | Keep upstream compatibility constraints in the accompanying dependency refresh | Some dependencies remain below their latest release | [Dependency compatibility](#dependency-compatibility) |
| D4 | Accepted | Reuse compiled Mix output and npm downloads across compatible CI runs | Cache transfers consume storage and time; runtime or lock changes can require a cold build | [Build caching](#build-caching) |
| D2 | Superseded | Temporarily use Elixir 1.19.5 / OTP 26.2.5.21 | Prevented current HTTPoison/Hackney upgrades; replaced by D1 | [Original failure](#context) |

## Context

Taskman's Elixir workflow is defined in `.github/workflows/elixir.yml`. GitHub does not provide an
Alpine hosted-runner label, but it supports running job steps inside a container while a Linux
runner supplies Docker. See GitHub's
[job container documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/run-jobs-in-a-container).

The first Alpine workflow used the then-current Elixir 1.20.3 and Erlang/OTP 29.0.5 image. Hosted
runs exposed three independent boundaries:

1. [Run 31395719161](https://github.com/Incanus3/taskman/actions/runs/31395719161/job/93478006283)
   failed because the minimal image lacked Git, which Mix needs for Taskman's GitHub dependencies.
2. An isolated reproduction then showed that tests require a PostgreSQL service addressable through
   its container-network service name rather than `localhost`.
3. [Run 31397306552](https://github.com/Incanus3/taskman/actions/runs/31397306552/job/93483263412)
   aborted during BEAM startup. OTP 27 and later size the native-code alternate signal stack from
   musl's static 8 KiB `SIGSTKSZ`; newer x64 runner CPUs can require a larger stack. The root cause
   is documented in [Erlang/OTP issue 11248](https://github.com/erlang/otp/issues/11248).

The complete upstream correction was merged in
[Erlang/OTP pull request 11376](https://github.com/erlang/otp/pull/11376) on 2026-08-10, after OTP
29.0.5 was released. Both the original correction and the follow-up are included in
[OTP 29.1](https://github.com/erlang/otp/releases/tag/OTP-29.1), released on 2026-09-16.
OTP 29.1.2 contains that merged correction, and HexPM publishes the compatible
`hexpm/elixir:1.20.4-erlang-29.1.2-alpine-3.24.3` image. This supersedes the temporary OTP 26
fallback while preserving the x64 Alpine job topology.

An ARM64 runner was evaluated as a bridge, but
[run 31397873475](https://github.com/Incanus3/taskman/actions/runs/31397873475/job/93485172153)
confirmed that GitHub supports JavaScript actions in Alpine job containers only on x64 Linux
runners. Keeping ARM64 would require replacing standard checkout and cache actions.

## Requirements and decisions

- Execute build and test steps inside an Alpine Linux container.
- Use Elixir 1.20.4 and Erlang/OTP 29.1.2, which includes the complete signal-stack correction.
- Pin the complete image tag
  `hexpm/elixir:1.20.4-erlang-29.1.2-alpine-3.24.3`.
- Use `runs-on: ubuntu-26.04` as the GitHub-hosted x64 Docker host. Ordinary `run` steps still
  execute inside Alpine.
- Use `actions/checkout@v7` and `actions/cache@v6`, whose declared action runtime is Node.js 24.
  Cache dependency sources and compiled output under compatible build keys. These versions follow GitHub's
  [Node.js 20 retirement guidance](https://github.blog/changelog/2025-09-19-deprecation-of-node-20-on-github-actions-runners/).
- Select Ubuntu 26.04 explicitly so the [host image migration](https://github.com/actions/runner-images/issues/14748)
  is verified deliberately rather than arriving through the floating `ubuntu-latest` label.
- Install Alpine's `git` and GNU `tar` packages before checkout and caching. BusyBox `tar` does not
  support the cache action's required `--posix` option.
- Run PostgreSQL as a healthy service container and expose its `postgres` service label through
  `POSTGRES_HOST`. Local tests continue to default to `localhost`.
- Preserve the workflow's triggers, permissions, dependency installation, and test command.
- Let Wallaby select current HTTPoison and Hackney releases now that OTP 26 compatibility is no
  longer required. Remove the direct HTTPoison 2 compatibility pin; do not add an application HTTP
  client. Application HTTP calls continue to use Req.

## Rejected alternatives

- `runs-on: alpine-latest` is invalid because GitHub does not offer that hosted runner label.
- Retaining Elixir 1.20 while downgrading to OTP 26 is unsupported. Elixir 1.20 supports OTP 27–29;
  Elixir 1.19 supports OTP 26–28.
- Downgrading to an uncorrected OTP 27 or 28 release does not avoid the original bug because the
  affected signal-stack behavior begins in OTP 27.
- Using ARM64 would require removing or replacing standard JavaScript actions in Alpine containers.
- Disabling container security, injecting an `LD_PRELOAD` shim, or maintaining a custom patched OTP
  image would add security or maintenance cost for a temporary upstream defect.
- Floating image tags would silently change CI behavior and could reintroduce the failure.

## File boundary and behavior

The CI job configuration is owned by `.github/workflows/elixir.yml`. The build job uses:

```yaml
runs-on: ubuntu-26.04
container:
  image: hexpm/elixir:1.20.4-erlang-29.1.2-alpine-3.24.3
env:
  POSTGRES_HOST: postgres
services:
  postgres:
    image: postgres:18.4-alpine3.24
```

The workflow installs its runtime packages through
`apk add --no-cache bash build-base fish git tar` before checkout and cache
restoration. This ensures both cache restore and post-job cache save use GNU tar. `config/test.exs`
reads `POSTGRES_HOST` with `localhost` as its default so the container job can use the service label
without changing local development behavior. No application source, production runtime
configuration, or product behavior changes.

`mix.exs` owns direct dependency requirements; `mix.lock` records the resolved HTTPoison/Hackney
versions and their transitive dependencies. Wallaby continues to own this browser-test HTTP client
chain. The development guide owns the CI/release runtime distinction.

## Build caching

The Mix cache contains `deps/` and `_build/`, including the separate `dev` and `test` outputs and
asset-tool downloads. Its key includes runner OS/architecture, a cache-format namespace, the hash
of the complete workflow plus `mix.lock`, and the commit SHA. The workflow hash covers the pinned
container/runtime and build commands, avoiding reuse across incompatible native builds without a
second manually maintained runtime version. Changes anywhere in the workflow conservatively
invalidate the compiled cache. Restore prefixes retain the same compatibility hash; do not add a
broad cross-runtime or cross-lock fallback.

The SHA suffix lets successful newer commits save refreshed output because GitHub caches are
immutable. Mix still runs its normal dependency checks and compilation; cache hits never skip
assets or tests. A miss follows the ordinary full build. Keeping the dependency sources with their
compiled output also preserves their associated file timestamps.

A separate cache stores npm's actual download-cache directory, discovered with `npm config get
cache`, keyed by OS/architecture and `assets/package-lock.json`. `npm ci` still reconstructs
`node_modules` from the lockfile; do not cache `node_modules` or installed application state.

These choices extend the earlier dependency-source-only cache. Caching only downloads avoids
compiled-state reuse but leaves the expensive Mix compilation repeated. Replacing the runner or
maintaining a custom prebuilt image would add a separate runtime lifecycle for this optimization.
The existing Alpine runtime, service topology, triggers and test commands remain authoritative.

GitHub's [cache semantics and access scope](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)
apply: pull-request caches are scoped to their merge ref, so a successful PR cache does not seed
`main` automatically. A successful `main` run seeds caches that later branches can restore. Store
no secrets or private operational evidence in either cache. Cache reuse improves build work; it
does not replace hosted correctness verification or establish a fixed timing improvement.

## Dependency compatibility

The accompanying dependency refresh uses stable versions supported by the existing dependency tree.
Ash Authentication constrains Assent below 0.3; pyinfra 3.10.0 constrains Paramiko below 5,
python-dateutil below 3, and types-paramiko below 5. These constraints are accepted for this refresh:
forcing newer versions would bypass declared compatibility and require separately scoped migration
or override verification, including authentication and deployment behavior. This decision does not
prevent a later upgrade when compatibility is established.

## Verification

- Parse the workflow as YAML.
- Confirm the workflow contains the x64 runner, pinned compatible image, checkout action, cache
  action, Git and GNU tar installation, and PostgreSQL service contract.
- Confirm the pinned image reports Alpine 3.24.3, Elixir 1.20.4, and Erlang/OTP 29.1.2.
- Confirm dependency resolution and tests succeed in the exact job/service container topology.
- Run `mix precommit`.
- Exercise a cache miss followed by restoration from a compatible later commit and saving under
  its new SHA. Inspect restore/save logs and compare build/test step durations before claiming
  cross-commit reuse. A same-revision rerun alone proves only exact-key restoration.
- Treat a completed successful GitHub-hosted run as the final acceptance gate.

## Acceptance boundary

Hosted acceptance must exercise the pinned runtime and dependency set in the actual x64 job and
PostgreSQL service topology. A local container run alone does not establish hosted-runner startup
compatibility. This gate covers CI behavior; browser interaction acceptance and the separately
pinned release runtime require their own verification.
