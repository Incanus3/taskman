# Taskman

Taskman is an authenticated shared workspace for organizing Projects, Lists, and Tasks. Explicitly
provisioned users can use its LiveView browser interface and API-key-authenticated CLI from
arbitrary clients; Taskman does not assign ownership or permissions to individual domain
records. The MVP product definition and delivery context live in [the project documentation](docs/README.md).

For a dedicated HTTPS host, use the repository-owned `./ops/taskman` automation and follow the
self-contained [deployment runbook](docs/guides/deployment.md). It provisions and operates the supported
Ubuntu host while retaining Taskman's OTP release, Caddy, systemd, and loopback PostgreSQL
topology. Existing Caddy main configuration and other sites stay operator-owned; Taskman manages
its reserved site. Provisioning can return exit `13` when application setup passes but proxy
activation/public verification remains pending; follow the runbook's
[activation and exact-target retry procedure](docs/guides/deployment.md#activate-a-pending-taskman-site).
Repository-bundled [operations reference skill](priv/taskman_ops_skill/SKILL.md) covers the
`./ops/taskman` controller; the application CLI skill remains separate. VPS creation, DNS, provider
firewall, Resend, and off-host backups remain operator-owned.
Local source development remains supported as described below.

## Local setup

1. Start PostgreSQL with the repository helper:

   ```sh
   ./run_postgres.sh
   ```

   This is the standard local setup path. It is optional when a compatible PostgreSQL instance is
   already running on `localhost:5432` with the `postgres` user and password.

2. Install dependencies, create and migrate the database, and build assets:

   ```sh
   mix setup
   ```

3. Start the application:

   ```sh
   mix phx.server
   ```

4. Sign in at [`localhost:4000`](http://localhost:4000) with either development account:

   - Administrator: `admin@taskman.dev`
   - Standard user: `user@taskman.dev`
   - Password for both: `taskman-dev`

   The seed script creates missing development accounts without overwriting existing accounts.
   It replaces existing Projects, Lists, and Tasks with three example Projects, seven Lists, and
   30 Tasks. Two example Projects are empty; Taskman Demo contains the Lists and Tasks.
   To create another local administrator, run `mix taskman.accounts.create-admin` in another
   terminal.

## Local CLI

The installable `taskman` escript talks to the running authenticated API. Build and install it from
the repository with:

```sh
mix do escript.build + escript.install --force
~/.mix/escripts/taskman agent onboarding
```

Mix installs the executable at `~/.mix/escripts/taskman`. Add `~/.mix/escripts` to `PATH` to use
the bare `taskman` command; the full path above works without that entry. Create an API key from
Account settings, then configure the CLI without putting the key in a command or URL:

```sh
taskman config hosts add local --url http://localhost:4000
taskman config hosts set-key local
taskman config default-host local
taskman projects list --json
```

For a hosted server, register its HTTPS URL instead. Fresh configuration has no implicit endpoint;
registration alone creates neither a credential nor a default. Keys are shown once, stored in
protected XDG configuration, and never printed. Ordinary API commands return status `7` when
required authentication is unavailable. Select hosts with global `--host NAME` before or after
the command, a repository binding, `TASKMAN_HOST`, or the configured default, in that order.
Injected secrets require both `TASKMAN_API_KEY` and matching `TASKMAN_API_KEY_HOST`.

After inspecting or creating a Project, bind a Git clone to its exact ID:

```sh
taskman repo bind --host local --project 7
taskman repo show
taskman tasks list --include-descendants --json
```

The binding is shared by linked worktrees; independent clones bind separately. It contains only
the host name and Project ID, while credentials remain in user configuration. Bound Lists and
Tasks use this Project by default. Explicit overrides require both `--host` and `--project`, even
for matching values. Unbound folders require `--project` for scoped commands. Search inherits the
bound Project; `tasks search QUERY --all-projects` searches every Project on the selected host,
and conflicts with `--project`. `--host NAME --all-projects` permits a host-only search override.

Local host management and `repo show`/`repo unbind` work offline. Removing a host clears its default
and leaves clone references intact. Inspect with `repo show`, then explicitly rebind, unbind, or
re-register the same logical backend. Use `config hosts set-url NAME --url URL` only for an address
change of the same backend: all bound repositories follow it and the API key is retained. A
different backend needs a new name and explicit rebinding.

Update the binary before the first config mutation. Valid legacy URL/key files are read as host
`default` without writes; an explicit config mutation upgrades them to version 2, which old
binaries cannot read. Replace unqualified `config set-url`/`config set-key` with named-host
commands, and raw `--api-url`/`TASKMAN_API_URL` targeting with registered hosts. Run
`taskman agent onboarding` for setup and recovery, or `taskman --help` for command discovery.

## Development

Run the full local verification suite with:

```sh
mix precommit
```

## Learn more

- Official website: https://www.phoenixframework.org/
- Guides: https://phoenix.hexdocs.pm/overview.html
- Docs: https://phoenix.hexdocs.pm
- Forum: https://elixirforum.com/c/phoenix-forum
- Source: https://github.com/phoenixframework/phoenix
