# Alpine Elixir CI Implementation Plan

**Goal:** Run the existing Elixir CI job inside a pinned Alpine image containing Elixir 1.20.3 and
Erlang/OTP 29.0.5.

**Design authority:** `docs/specs/2026-08-10-alpine-elixir-ci-design.md`

**Work tracking:** `tas-alpine-elixir-ci-6tk` (closed)

**Architecture:** Keep GitHub's `ubuntu-latest` host so the hosted runner can launch Docker, but run
ordinary job steps inside the exact HexPM Alpine image selected in the design. The image replaces the
separate toolchain setup action.

**Technology:** GitHub Actions, Alpine Linux 3.24.1, Elixir 1.20.3, Erlang/OTP 29.0.5

## Constraints

- Modify only `.github/workflows/elixir.yml` during implementation.
- Preserve triggers, permissions, checkout, caching, dependency installation, and the test command.
- Do not introduce a floating image tag or an invalid `alpine-latest` runner label.
- Do not expand this task to add services or update unrelated actions.

## Task 1: Select the Alpine toolchain image

**Files:**

- Modify: `.github/workflows/elixir.yml`

**Result:** The `build` job runs inside
`hexpm/elixir:1.20.3-erlang-29.0.5-alpine-3.24.1` and no longer installs a second Elixir/OTP
toolchain.

- [x] Mark `tas-alpine-elixir-ci-6tk` in progress.

- [x] Add the container immediately after `runs-on`:

  ```yaml
  runs-on: ubuntu-latest
  container:
    image: hexpm/elixir:1.20.3-erlang-29.0.5-alpine-3.24.1
  ```

- [x] Delete the complete `Set up Elixir` step, including its `uses` and `with` entries. Leave every
  other step unchanged.

- [x] Parse and structurally inspect the workflow:

  ```bash
  python3 - <<'PY'
  from pathlib import Path
  import yaml

  workflow = yaml.safe_load(Path(".github/workflows/elixir.yml").read_text())
  build = workflow["jobs"]["build"]
  assert build["runs-on"] == "ubuntu-latest"
  assert build["container"]["image"] == (
      "hexpm/elixir:1.20.3-erlang-29.0.5-alpine-3.24.1"
  )
  assert all("erlef/setup-beam" not in str(step) for step in build["steps"])
  PY
  ```

  Expected: exit status 0 with no output.

- [x] Verify the pinned image:

  ```bash
  docker run --rm hexpm/elixir:1.20.3-erlang-29.0.5-alpine-3.24.1 \
    sh -lc 'grep "^VERSION_ID=3.24.1" /etc/os-release && elixir --version'
  ```

  Expected: Alpine `VERSION_ID=3.24.1`, Erlang/OTP 29, and Elixir 1.20.3.

- [x] Run the repository verification gate:

  ```bash
  mix precommit
  ```

  Expected: exit status 0. If PostgreSQL or another pre-existing external dependency is unavailable,
  record the exact failing command and error without changing workflow scope.

- [x] Inspect the final diff and confirm `.github/workflows/elixir.yml` is the only implementation
  file changed and contains no planning terminology.

- [x] Commit the workflow change with message `run Elixir CI in Alpine container`, then close
  `tas-alpine-elixir-ci-6tk` with the verification evidence.

## Completion

Implemented in commit `805d3f3`. Fresh structural, container, and `mix precommit` verification
passed on 2026-08-10; the Beads task contains the detailed evidence and independent review result.
