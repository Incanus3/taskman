# Alpine Elixir CI

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
29.0.5 was released. It is therefore not present in any stable affected release available for this
workflow yet.

An ARM64 runner was evaluated as a bridge, but
[run 31397873475](https://github.com/Incanus3/taskman/actions/runs/31397873475/job/93485172153)
confirmed that GitHub supports JavaScript actions in Alpine job containers only on x64 Linux
runners. Keeping ARM64 would require replacing standard checkout and cache actions.

## Requirements and decisions

- Execute build and test steps inside an Alpine Linux container.
- Use the newest mutually supported versions from before the affected OTP behavior:
  Elixir 1.19.5 and Erlang/OTP 26.2.5.21.
- Pin the complete image tag
  `hexpm/elixir:1.19.5-erlang-26.2.5.21-alpine-3.24.1`.
- Keep `runs-on: ubuntu-latest` as the GitHub-hosted x64 Docker host. Ordinary `run` steps still
  execute inside Alpine.
- Preserve `actions/checkout@v4` and the existing dependency cache.
- Install Alpine's `git` and GNU `tar` packages before checkout and caching. BusyBox `tar` does not
  support the cache action's required `--posix` option.
- Run PostgreSQL as a healthy service container and expose its `postgres` service label through
  `POSTGRES_HOST`. Local tests continue to default to `localhost`.
- Preserve the workflow's triggers, permissions, dependency installation, and test command.
- Track a separate follow-up to upgrade Elixir and OTP after a stable OTP release contains the
  alternate-signal-stack correction.

## Rejected alternatives

- `runs-on: alpine-latest` is invalid because GitHub does not offer that hosted runner label.
- Retaining Elixir 1.20 while downgrading to OTP 26 is unsupported. Elixir 1.20 supports OTP 27–29;
  Elixir 1.19 supports OTP 26–28.
- Keeping OTP 27 or 28 does not avoid the bug because the affected signal-stack behavior begins in
  OTP 27.
- Using ARM64 would require removing or replacing standard JavaScript actions in Alpine containers.
- Disabling container security, injecting an `LD_PRELOAD` shim, or maintaining a custom patched OTP
  image would add security or maintenance cost for a temporary upstream defect.
- Floating image tags would silently change CI behavior and could reintroduce the failure.

## File boundary and behavior

The CI job configuration is owned by `.github/workflows/elixir.yml`. The build job uses:

```yaml
runs-on: ubuntu-latest
container:
  image: hexpm/elixir:1.19.5-erlang-26.2.5.21-alpine-3.24.1
env:
  POSTGRES_HOST: postgres
services:
  postgres:
    image: postgres:18.4-alpine3.24
```

The workflow installs Git and GNU tar through `apk add --no-cache git tar` before checkout and cache
restoration. This ensures both cache restore and post-job cache save use GNU tar. `config/test.exs`
reads `POSTGRES_HOST` with `localhost` as its default so the container job can use the service label
without changing local development behavior. No application source, production runtime
configuration, or product behavior changes.

## Verification

- Parse the workflow as YAML.
- Confirm the workflow contains the x64 runner, pinned compatible image, checkout action, cache
  action, Git and GNU tar installation, and PostgreSQL service contract.
- Confirm the pinned image reports Alpine 3.24.1, Elixir 1.19.5, and Erlang/OTP 26.
- Confirm dependency resolution and tests succeed in the exact job/service container topology.
- Run `mix precommit`.
- Treat a completed successful GitHub-hosted run as the final acceptance gate.

## Upgrade follow-up

Upgrade only after Erlang publishes a stable OTP release containing pull request 11376 and HexPM
publishes a compatible Alpine image. Select the then-current stable Elixir release and a supported
fixed OTP release, return no earlier than OTP 27, and verify the exact x64 hosted failure path before
closing the follow-up. The runner, checkout action, cache, runtime packages, and PostgreSQL service
should remain unchanged during that upgrade.
