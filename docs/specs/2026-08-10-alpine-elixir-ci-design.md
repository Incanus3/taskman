# Alpine Elixir CI

## Context

Taskman's Elixir CI workflow is defined in `.github/workflows/elixir.yml`. It currently runs directly
on GitHub's `ubuntu-latest` hosted runner and installs Elixir 1.15.2 with Erlang/OTP 26.0 through
`erlef/setup-beam`.

GitHub-hosted Actions does not provide an Alpine runner label. GitHub supports running a job's steps
inside a container through `jobs.<job_id>.container.image`, while the job still names a Linux host in
`runs-on`. See GitHub's
[job container documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/run-jobs-in-a-container).

As of 2026-08-10, the latest stable releases are Elixir 1.20.3 and Erlang/OTP 29.0.5. Elixir 1.20.3
is listed in the [Elixir releases](https://github.com/elixir-lang/elixir/releases), and OTP 29.0.5 is
listed in the [Erlang/OTP releases](https://github.com/erlang/otp/releases). HexPM publishes the
compatible Alpine image
`hexpm/elixir:1.20.3-erlang-29.0.5-alpine-3.24.1`.

## Requirements and decisions

- Execute the build and test steps inside an Alpine Linux container.
- Use Elixir 1.20.3 and Erlang/OTP 29.0.5.
- Pin the complete container tag so CI remains reproducible. Future language or operating-system
  upgrades require an explicit workflow change.
- Keep `runs-on: ubuntu-latest` as the GitHub-hosted Docker host. This does not make the build
  environment Ubuntu; ordinary job steps execute inside the configured Alpine container.
- Remove `erlef/setup-beam`, because the selected image already contains the required Elixir and OTP
  versions.
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

Only `.github/workflows/elixir.yml` needs an implementation change. The `build` job will gain:

```yaml
container:
  image: hexpm/elixir:1.20.3-erlang-29.0.5-alpine-3.24.1
```

The existing `Set up Elixir` step will be deleted. No application source, dependencies, runtime
configuration, or product behavior changes.

## Verification

- Parse the edited workflow as YAML.
- Confirm the pinned container reports Elixir 1.20.3 and Erlang/OTP 29.0.5.
- Run the workflow's dependency and test commands in the selected container where local Docker
  support permits it.
- Run `mix precommit` after all repository changes, as required by project guidance. If an existing
  external dependency such as PostgreSQL prevents a check, record the exact failure rather than
  broadening this change to redesign the workflow.

## Caveats and implementation checklist

GitHub still schedules the container on `ubuntu-latest`; that host is an implementation detail of the
hosted runner. JavaScript actions execute through GitHub's container-action support, while `run`
steps use the container's default `sh` shell.

Implementation consists of adding the pinned job container, deleting the now-redundant setup step,
and performing the verification above. The workflow should not be expanded with unrelated service,
cache, action-version, or application changes.
