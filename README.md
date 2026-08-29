# Taskman

Taskman is a single-user, locally run project manager for organizing Tasks and coordinating local
Auggie agent sessions. The MVP product definition and delivery context live in [the project
documentation](docs/README.md).

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

Visit [`localhost:4000`](http://localhost:4000).

## Local CLI

The installable `taskman` escript talks to the running local API. Build and install it from the
repository with:

```sh
mix do escript.build + escript.install --force
~/.mix/escripts/taskman agent onboarding
```

Mix installs the executable at `~/.mix/escripts/taskman`. Add `~/.mix/escripts` to `PATH` to use
the bare `taskman` command; the full path above works without that entry. Run `taskman --help` for
the command groups, options, configuration, and examples.

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
