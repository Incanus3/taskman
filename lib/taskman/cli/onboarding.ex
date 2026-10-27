defmodule Taskman.CLI.Onboarding do
  @moduledoc "Versioned, offline onboarding guidance for people and agents."

  @text """
  Taskman is an authenticated shared system of record for Projects, Lists, and Tasks. Explicitly provisioned users work in the same workspace; Tasks do not have per-user ownership or collaboration permissions. Taskman keeps Projects, nested lists, and task titles, descriptions, priorities, due dates, lifecycle states, and locations together. The CLI can list, inspect, create, and update Projects; list, inspect, create, and rename Lists; and list, inspect, create, update, and move Tasks within a Project.

  Installation

  Build and install the version-matched escript from the Taskman repository:
  mix do escript.build + escript.install --force

  Mix installs the executable in the escripts directory under MIX_HOME (default: ~/.mix). Put that directory on PATH to invoke it as taskman:
  export PATH="${MIX_HOME:-$HOME/.mix}/escripts:$PATH"

  Invoke the installed executable directly when that directory is not on PATH:
  "${MIX_HOME:-$HOME/.mix}/escripts/taskman" --help

  Backend

  Ordinary commands require a running backend. From the Taskman repository, start the Phoenix application with:
  mix phx.server

  The CLI does not start the backend for you. Completion generation and onboarding are offline and work while the backend is stopped.

  API configuration

  The CLI reads ${XDG_CONFIG_HOME:-$HOME/.config}/taskman/config.json. Fresh configuration has no implicit host or endpoint. Register a hosted HTTPS URL, enter an API key without echo, and explicitly choose a default:
  taskman config hosts add personal --url https://taskman.example.com
  taskman config hosts set-key personal
  taskman config default-host personal
  taskman config show

  For local development, register http://localhost:4000 under a named host instead. Registration alone neither selects a default nor creates a Project. Create an API key in the browser from Account settings. Copy it when it is shown: its plaintext value is one-time only. Do not put the key in a command, URL, log, or report. taskman config show confirms effective context without revealing it; config hosts list/show describe stored credentials only.

  Host selection is --host NAME, repository binding, nonempty TASKMAN_HOST, then configured default. Global --host may appear before or after the command. For CI, containers, or secret injection, pair TASKMAN_API_KEY with TASKMAN_API_KEY_HOST matching the selected registered host. The label associates the secret, and never selects an endpoint. Incomplete or mismatched pairs refuse locally. Ordinary API commands require a key and return status 7 when authentication is missing, rejected, or forbidden.

  Repository context

  Create or inspect the Project first, then run these commands inside a Git working tree:
  taskman projects create --name "My Project" --json
  taskman repo bind --host personal --project 7
  taskman repo show
  taskman tasks list --json

  Bind validates the authenticated Project before writing. The clone binding stores only host name and Project ID in Git's common metadata; linked worktrees share it, while independent clones bind separately. It supplies context, not permission to mutate or change lifecycle state.

  Bound scoped commands need no repeated Project. To override, provide both --host and --project, even when they match the binding:
  taskman tasks list --host personal --project 9 --json
  Unbound Git clones and non-Git folders require --project for Lists and Tasks; there is no default Project. The representative Project-explicit examples below assume an unbound folder; add --host personal when overriding a bound clone.

  Search inherits the bound Project. Use the explicit escape to search all Projects on the selected host:
  taskman tasks search publish --all-projects
  taskman tasks search publish --host personal --all-projects
  --all-projects conflicts with --project. Host-only Project commands do not inherit the bound Project ID.

  Recovery and upgrade

  Host management and repo show/unbind work offline, ignoring transport-selection environment settings. Removing a host clears its default but leaves clone bindings intact. repo show reports dangling references. Re-register the same logical backend under its old name, bind explicitly to another host/Project, or remove the clone reference:
  taskman repo unbind
  For malformed binding data, unbind offline before binding again. Complete explicit host/Project pairs bypass binding discovery for resource commands.

  Change an address only for the same logical backend:
  taskman config hosts set-url personal --url https://taskman.example.com
  All repositories bound to this host use the updated URL. API key retained. Different backends need different host names and explicit rebinding.

  Update the binary before the first configuration mutation. Legacy URL/key settings are read as virtual host default without writes; a successful user-config mutation upgrades to version 2. Old binaries cannot read that format. Key-only legacy settings retain their historical localhost endpoint; empty settings select no host. Replace old config set-url/set-key commands with config hosts commands; replace raw --api-url or TASKMAN_API_URL targeting with registered hosts. There is no automatic downgrade.

  Representative workflows

  Use --json when an agent needs one machine-readable API-compatible data envelope on stdout. Diagnostics go to stderr.
  Project description, icon, and color give each Project a recognizable identity. Creation can set them, and update changes only supplied fields.
  taskman projects list --json
  taskman projects create --name "My Project" --json
  taskman projects create --name "My Project" --description Delivery --icon rocket-launch --color '#6366F1' --json
  taskman projects update 7 --description Revised --json
  taskman lists list --project 7 --json
  taskman lists create --project 7 --name Planning --json
  taskman tasks list --project 7 --json
  taskman tasks create --project 7 --title "Prepare launch" --status pending --json
  taskman tasks update --project 7 42 --status in_progress --json
  taskman tasks update --project 7 42 --parent 41 --json
  taskman tasks hierarchy --project 7 42 --json
  taskman tasks move --project 7 42 --to-project-root --json
  taskman tasks show --project 7 42 --include-comments --json
  taskman tasks comments list --project 7 42 --json
  taskman tasks comments add --project 7 42 --text "Review notes" --author-name "Research agent" --json

  Read the Task, its thread and blockers before acting. Explicit assignment to a nonterminal Task authorizes bounded updates within its agreed scope, evidence comments, and explicit transitions to In Progress or In Review. Inspect the returned comment and list the thread afterward to verify it. The API key selects the verified posting account. --author-name supplies a truthful display name; it cannot change the posting account. Done, Will Not Do, reopening terminal work, unresolved-blocker overrides, deletion and material scope/priority changes require explicit human decisions by the operator. Runtime activity and passed checks never complete work automatically.

  In registered repositories, use the bound Taskman Project for execution tracking and retain migrated Beads as a read-only archive. An unbound repository keeps its existing tracker. Failed context lookup, malformed/dangling binding, authentication failure or unavailable backend must not trigger fallback to Beads. Discovery alone does not authorize execution. Use tasks list --include-descendants and inspect tasks blocking show for Pending eligibility; Done and Will Not Do blockers are resolved. There is no computed ready queue or atomic claim. Coordinate ownership before parallel work; In Progress is not a lock.

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
