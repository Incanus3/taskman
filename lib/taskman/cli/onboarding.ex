defmodule Taskman.CLI.Onboarding do
  @moduledoc "Versioned, offline onboarding guidance for people and agents."

  @text """
  Taskman is a local, single-user system of record for Projects, Lists, and Tasks. It keeps project directories, nested lists, and task titles, descriptions, priorities, due dates, lifecycle states, and locations together. The CLI can list, inspect, and create Projects; list, inspect, create, and rename Lists; and list, inspect, create, update, and move Tasks within a Project.

  Installation

  Build and install the version-matched escript from the Taskman repository:
  mix do escript.build + escript.install --force

  Mix installs the executable at ~/.mix/escripts/taskman. Put ~/.mix/escripts on PATH to invoke it as taskman:
  export PATH="$HOME/.mix/escripts:$PATH"

  The file ~/.mix/escripts/taskman can also be invoked directly when that directory is not on PATH.

  Backend

  Ordinary commands require a running backend. From the Taskman repository, start the Phoenix application with:
  mix phx.server

  In short, ordinary commands require a running backend; completion generation and onboarding remain available offline.

  The CLI does not start the backend for you. Completion generation and onboarding are offline and work while the backend is stopped.

  API configuration

  The default API URL is http://localhost:4000. Set TASKMAN_API_URL to use another local server, or pass --api-url URL. An explicit --api-url takes precedence over TASKMAN_API_URL, which takes precedence over the default.

  Representative workflows

  Use --json when an agent needs one machine-readable API-compatible data envelope on stdout. Diagnostics go to stderr.
  taskman projects list --json
  taskman projects create --name "My Project" --directory /work/my-project --json
  taskman lists list --project 7 --json
  taskman lists create --project 7 --name Planning --json
  taskman tasks list --project 7 --json
  taskman tasks create --project 7 --title "Prepare launch" --status pending --json
  taskman tasks update --project 7 42 --status in_progress --json
  taskman tasks move --project 7 42 --to-project-root --json

  Install shell completions by redirecting the generated source to the standard location:
  mkdir -p ~/.local/share/bash-completion/completions
  taskman completions bash > ~/.local/share/bash-completion/completions/taskman

  mkdir -p ~/.config/fish/completions
  taskman completions fish > ~/.config/fish/completions/taskman.fish

  Completions are static, registry-derived, and do not query the backend or complete resource IDs and names.

  Agent guidance

  Install the bundled, version-matched agent skill with:
  taskman agent skill install
  Use taskman agent skill install --force only when intentionally replacing an unrecognized target. The skill is installed at ~/.agents/skills/taskman-cli/.

  Start with taskman --help to discover every command, option, example, and exit status. Use taskman <group> --help or taskman <group> <command> --help for focused guidance. Check connectivity after starting the backend, and treat the API as authoritative for resource identity and validation.
  """

  @doc "Return the complete offline onboarding text."
  @spec text() :: String.t()
  def text, do: @text
end
