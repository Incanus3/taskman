# Alpine Elixir CI

## Context

Before this work, Taskman's Elixir CI workflow ran directly on GitHub's `ubuntu-latest` hosted runner
and installed Elixir 1.15.2 with Erlang/OTP 26.0 through `erlef/setup-beam`. The workflow is defined
in `.github/workflows/elixir.yml`.

GitHub-hosted Actions does not provide an Alpine runner label. GitHub supports running a job's steps
inside a container through `jobs.<job_id>.container.image`, while the job still names a Linux host in
`runs-on`. See GitHub's
[job container documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/run-jobs-in-a-container).

As of 2026-08-10, the latest stable releases are Elixir 1.20.3 and Erlang/OTP 29.0.5. Elixir 1.20.3
is listed in the [Elixir releases](https://github.com/elixir-lang/elixir/releases), and OTP 29.0.5 is
listed in the [Erlang/OTP releases](https://github.com/erlang/otp/releases). HexPM publishes the
compatible Alpine image
`hexpm/elixir:1.20.3-erlang-29.0.5-alpine-3.24.1`.

The first landed container workflow failed in
[GitHub Actions run 31395719161](https://github.com/Incanus3/taskman/actions/runs/31395719161/job/93478006283)
because the image had no `git` executable. After Git was installed in an isolated reproduction,
dependency resolution succeeded and exposed the next missing boundary: tests could not connect to
PostgreSQL because a job container cannot reach a host service through `localhost`. A local
two-container reproduction using the job and service images confirmed the complete dependency and
test path.

## Requirements and decisions

- Execute the build and test steps inside an Alpine Linux container.
- Use Elixir 1.20.3 and Erlang/OTP 29.0.5.
- Pin the complete container tag so CI remains reproducible. Future language or operating-system
  upgrades require an explicit workflow change.
- Keep `runs-on: ubuntu-latest` as the GitHub-hosted Docker host. This does not make the build
  environment Ubuntu; ordinary job steps execute inside the configured Alpine container.
- Remove `erlef/setup-beam`, because the selected image already contains the required Elixir and OTP
  versions.
- Install Alpine's `git` package before dependency resolution. The HexPM image does not include Git,
  while Taskman fetches `heroicons` and `daisyui` from GitHub.
- Run PostgreSQL as a healthy service container and expose its `postgres` service label to the test
  configuration through `POSTGRES_HOST`. Local tests continue to default to `localhost`.
- Preserve the workflow's triggers, permissions, checkout, dependency cache, dependency installation,
  and test command.

## Rejected alternatives

- `runs-on: alpine-latest` is invalid because GitHub does not offer that hosted runner label.
- Installing `elixir` and `erlang` from Alpine's package repositories would couple the toolchain
  versions to the distribution repository and provide less explicit control over their compatibility.
- Floating image or tool versions would silently change CI behavior between runs and could select
  prereleases. The operator chose exact current stable versions instead.
- Building a project-owned CI image or resolving image tags dynamically would add maintenance without
  improving this workflow's current requirements.

## File boundary and behavior

The CI job configuration is owned by `.github/workflows/elixir.yml`. The `build` job uses:

```yaml
container:
  image: hexpm/elixir:1.20.3-erlang-29.0.5-alpine-3.24.1
env:
  POSTGRES_HOST: postgres
services:
  postgres:
    image: postgres:18.4-alpine3.24
```

The workflow installs Git with `apk add --no-cache git` before `mix deps.get`. The existing
`Set up Elixir` step is deleted. `config/test.exs` reads `POSTGRES_HOST` with `localhost` as its
default so the container job can use the service label without changing local development behavior.
No application source, dependencies, production runtime configuration, or product behavior changes.

## Verification

- Parse the edited workflow as YAML.
- Confirm the pinned container reports Elixir 1.20.3 and Erlang/OTP 29.0.5.
- Confirm dependency resolution succeeds after Git is installed.
- Run the workflow's test command against the PostgreSQL service topology where local Docker support
  permits it.
- Run `mix precommit` after all repository changes, as required by project guidance. If an existing
  external dependency such as PostgreSQL prevents a check, record the exact failure rather than
  broadening this change to redesign the workflow.

## Caveats and implementation checklist

GitHub still schedules the container on `ubuntu-latest`; that host is an implementation detail of the
hosted runner. JavaScript actions execute through GitHub's container-action support, while `run`
steps use the container's default `sh` shell. Service containers share a Docker network with the job
container and are addressed by their service label rather than `localhost`.

Implementation consists of using the pinned job container, deleting the redundant setup step,
installing Git, providing the PostgreSQL service, and performing the verification above. The workflow
should not be expanded with unrelated services, cache, action-version, or application changes.
