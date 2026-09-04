# Taskman

Taskman is an authenticated shared workspace for organizing Projects, Lists, Tasks, and local agent
sessions. Explicitly provisioned users can use its LiveView browser interface and API-key-authenticated
CLI from arbitrary clients; Taskman does not assign ownership or permissions to individual domain
records. The MVP product definition and delivery context live in [the project documentation](docs/README.md).

For a dedicated HTTPS host, follow the self-contained [deployment runbook](docs/deployment.md).
It packages Taskman as an OTP release behind Caddy and systemd. Local source development remains
supported as described below.

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
taskman config set-url http://localhost:4000
taskman config set-key
taskman projects list --json
```

For a hosted server, configure its HTTPS URL instead. The key is shown once, is stored in protected
XDG configuration, and ordinary API commands return status `7` when authentication is unavailable.
Run `taskman --help` for command groups, options, configuration, and examples.

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
