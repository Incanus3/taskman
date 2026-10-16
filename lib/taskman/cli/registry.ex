defmodule Taskman.CLI.Registry do
  @moduledoc "The single declarative source of truth for the Taskman CLI."

  alias Taskman.CLI.Registry.{Argument, Command, Option}
  alias Taskman.Projects.Project

  @project_icons Project.icons()
  @statuses ~w(icebox pending in_progress in_review done will_not_do)
  @priorities ~w(none low medium high urgent)
  @task_sort_fields ~w(id title status priority location)
  @sort_directions ~w(asc desc)

  @doc "Lifecycle values accepted by the API and CLI."
  @spec statuses() :: [String.t()]
  def statuses, do: @statuses

  @doc "Priority values accepted by the API and CLI."
  @spec priorities() :: [String.t()]
  def priorities, do: @priorities

  @doc "All command records in their documented discovery order."
  @spec commands() :: [Command.t()]
  def commands do
    [
      %Command{
        path: ~w(projects list),
        summary: "List Projects.",
        usage: "taskman projects list",
        handler: {:projects, :list},
        context_role: :host_only,
        examples: ["taskman projects list", "taskman projects list --json"]
      },
      %Command{
        path: ~w(projects show),
        summary: "Inspect one Project by ID.",
        usage: "taskman projects show PROJECT_ID",
        handler: {:projects, :show},
        context_role: :host_only,
        arguments: [argument(:project_id, "PROJECT_ID", :positive_integer, "Project ID.")],
        examples: ["taskman projects show 7"]
      },
      %Command{
        path: ~w(projects create),
        summary: "Create a Project.",
        usage:
          "taskman projects create --name NAME [--description TEXT] [--icon ICON] [--color '#RRGGBB']",
        handler: {:projects, :create},
        context_role: :host_only,
        options: [
          option(:name, "--name", :string, "NAME", "Project name.", required?: true),
          option(:description, "--description", :string, "TEXT", "Project description."),
          option(:icon, "--icon", :string, "ICON", "Project icon.", values: @project_icons),
          option(:color, "--color", :string, "'#RRGGBB'", "Project color.")
        ],
        examples: ["taskman projects create --name CLI --icon rocket-launch --color '#6366F1'"]
      },
      %Command{
        path: ~w(projects update),
        summary: "Update a Project's identity.",
        usage:
          "taskman projects update PROJECT_ID [--name NAME] [--description TEXT] [--icon ICON] [--color '#RRGGBB']",
        handler: {:projects, :update},
        context_role: :host_only,
        arguments: [argument(:project_id, "PROJECT_ID", :positive_integer, "Project ID.")],
        options: [
          option(:name, "--name", :string, "NAME", "Replacement Project name."),
          option(:description, "--description", :string, "TEXT", "Replacement description."),
          option(:icon, "--icon", :string, "ICON", "Replacement icon.", values: @project_icons),
          option(:color, "--color", :string, "'#RRGGBB'", "Replacement color.")
        ],
        constraints: [{:at_least_one, [:name, :description, :icon, :color]}],
        examples: ["taskman projects update 7 --description ''"]
      },
      %Command{
        path: ~w(lists list),
        summary: "List a Project's Lists in tree order.",
        usage: "taskman lists list [--project PROJECT_ID]",
        handler: {:lists, :list},
        context_role: :project_required,
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID.")
        ],
        examples: ["taskman lists list --project 7"]
      },
      %Command{
        path: ~w(lists show),
        summary: "Inspect one List by ID.",
        usage: "taskman lists show [--project PROJECT_ID] LIST_ID",
        handler: {:lists, :show},
        context_role: :project_required,
        arguments: [argument(:list_id, "LIST_ID", :positive_integer, "List ID.")],
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID.")
        ],
        examples: ["taskman lists show --project 7 11"]
      },
      %Command{
        path: ~w(lists create),
        summary: "Create a root or child List.",
        usage: "taskman lists create [--project PROJECT_ID] --name NAME [--parent LIST_ID]",
        handler: {:lists, :create},
        context_role: :project_required,
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID."),
          option(:name, "--name", :string, "NAME", "List name.", required?: true),
          option(:parent, "--parent", :positive_integer, "LIST_ID", "Optional parent List ID.")
        ],
        examples: ["taskman lists create --project 7 --name Launch --parent 11"]
      },
      %Command{
        path: ~w(lists rename),
        summary: "Rename a List.",
        usage: "taskman lists rename [--project PROJECT_ID] LIST_ID --name NAME",
        handler: {:lists, :rename},
        context_role: :project_required,
        arguments: [argument(:list_id, "LIST_ID", :positive_integer, "List ID.")],
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID."),
          option(:name, "--name", :string, "NAME", "New List name.", required?: true)
        ],
        examples: ["taskman lists rename --project 7 11 --name Planning"]
      },
      %Command{
        path: ~w(tasks list),
        summary: "List Tasks for a Project or List.",
        usage:
          "taskman tasks list [--project PROJECT_ID] [--list LIST_ID] [--include-descendants] [--status STATUS]... [--sort FIELD --direction asc|desc]",
        handler: {:tasks, :list},
        context_role: :project_required,
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID."),
          option(:list, "--list", :positive_integer, "LIST_ID", "Optional List ID."),
          option(
            :include_descendants,
            "--include-descendants",
            :boolean,
            nil,
            "Include descendant List Tasks."
          ),
          option(:statuses, "--status", :string, "STATUS", "Include this lifecycle status.",
            values: @statuses,
            repeatable?: true
          ),
          option(:sort, "--sort", :string, "FIELD", "Sort field.", values: @task_sort_fields),
          option(:direction, "--direction", :string, "DIRECTION", "Sort direction.",
            values: @sort_directions
          )
        ],
        constraints: [{:together, [:sort, :direction]}],
        examples: [
          "taskman tasks list --project 7 --list 11 --include-descendants",
          "taskman tasks list --project 7 --status pending --status done --sort title --direction asc"
        ]
      },
      %Command{
        path: ~w(tasks search),
        summary: "Find Tasks by ID or title in the selected scope.",
        usage: "taskman tasks search QUERY [--project PROJECT_ID | --all-projects] [--json]",
        handler: {:tasks, :search},
        context_role: :project_filter,
        constraints: [{:mutually_exclusive, [:project, :all_projects]}],
        arguments: [argument(:query, "QUERY", :nonblank_string, "Task ID or title terms.")],
        options: [
          option(
            :all_projects,
            "--all-projects",
            :boolean,
            nil,
            "Search every Project on the host."
          ),
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Limit to one Project.")
        ],
        examples: [
          "taskman tasks search \"42 publish\"",
          "taskman tasks search publish --host personal --project 9",
          "taskman tasks search publish --host personal --all-projects"
        ]
      },
      %Command{
        path: ~w(tasks show),
        summary: "Inspect one Task by ID.",
        usage: "taskman tasks show [--project PROJECT_ID] TASK_ID [--include-comments]",
        handler: {:tasks, :show},
        context_role: :project_required,
        arguments: [argument(:task_id, "TASK_ID", :positive_integer, "Task ID.")],
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID."),
          option(
            :include_comments,
            "--include-comments",
            :boolean,
            nil,
            "Include the ordered comment thread."
          )
        ],
        examples: ["taskman tasks show --project 7 42 --include-comments"]
      },
      %Command{
        path: ~w(tasks blocking show),
        summary: "Show Tasks this Task blocks and Tasks blocking it.",
        usage: "taskman tasks blocking show [--project PROJECT_ID] TASK_ID",
        handler: {:tasks, :blocking_show},
        context_role: :project_required,
        arguments: [argument(:task_id, "TASK_ID", :positive_integer, "Selected Task ID.")],
        options: [
          option(
            :project,
            "--project",
            :positive_integer,
            "PROJECT_ID",
            "Selected Task's Project ID."
          )
        ],
        examples: ["taskman tasks blocking show --project 9 42"]
      },
      %Command{
        path: ~w(tasks blocks add),
        summary: "Add a directed Blocks link from the named Task to a target Task.",
        usage: "taskman tasks blocks add [--project PROJECT_ID] TASK_ID --target TARGET_TASK_ID",
        handler: {:tasks, :blocks_add},
        context_role: :project_required,
        arguments: [argument(:task_id, "TASK_ID", :positive_integer, "Blocking Task ID.")],
        options: [
          option(
            :project,
            "--project",
            :positive_integer,
            "PROJECT_ID",
            "Blocking Task's Project ID."
          ),
          option(
            :target,
            "--target",
            :positive_integer,
            "TARGET_TASK_ID",
            "Blocked Task ID, even in another Project.",
            required?: true
          )
        ],
        examples: ["taskman tasks blocks add --project 7 12 --target 42"]
      },
      %Command{
        path: ~w(tasks blocks remove),
        summary: "Remove a directed Blocks link from the named Task to a target Task.",
        usage:
          "taskman tasks blocks remove [--project PROJECT_ID] TASK_ID --target TARGET_TASK_ID",
        handler: {:tasks, :blocks_remove},
        context_role: :project_required,
        arguments: [argument(:task_id, "TASK_ID", :positive_integer, "Blocking Task ID.")],
        options: [
          option(
            :project,
            "--project",
            :positive_integer,
            "PROJECT_ID",
            "Blocking Task's Project ID."
          ),
          option(
            :target,
            "--target",
            :positive_integer,
            "TARGET_TASK_ID",
            "Blocked Task ID, even in another Project.",
            required?: true
          )
        ],
        examples: ["taskman tasks blocks remove --project 7 12 --target 42"]
      },
      %Command{
        path: ~w(tasks comments list),
        summary: "Read a Task's ordered comment thread.",
        usage: "taskman tasks comments list [--project PROJECT_ID] TASK_ID",
        handler: {:tasks, :comments_list},
        context_role: :project_required,
        arguments: [argument(:task_id, "TASK_ID", :positive_integer, "Task ID.")],
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID.")
        ],
        examples: ["taskman tasks comments list --project 7 42 --json"]
      },
      %Command{
        path: ~w(tasks comments add),
        summary: "Append a comment as the authenticated account.",
        usage:
          "taskman tasks comments add [--project PROJECT_ID] TASK_ID --text TEXT [--author-name NAME]",
        handler: {:tasks, :comments_add},
        context_role: :project_required,
        arguments: [argument(:task_id, "TASK_ID", :positive_integer, "Task ID.")],
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID."),
          option(:text, "--text", :string, "TEXT", "Comment text.", required?: true),
          option(
            :author_name,
            "--author-name",
            :string,
            "NAME",
            "Optional display name; posting account is unchanged."
          )
        ],
        examples: [
          "taskman tasks comments add --project 7 42 --text 'Review notes' --author-name 'Research agent'"
        ]
      },
      %Command{
        path: ~w(tasks hierarchy),
        summary: "Inspect a Task's connected hierarchy.",
        usage: "taskman tasks hierarchy [--project PROJECT_ID] TASK_ID",
        handler: {:tasks, :hierarchy},
        context_role: :project_required,
        arguments: [argument(:task_id, "TASK_ID", :positive_integer, "Task ID.")],
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID.")
        ],
        examples: ["taskman tasks hierarchy --project 7 42"]
      },
      %Command{
        path: ~w(tasks create),
        summary: "Create a Task.",
        usage:
          "taskman tasks create [--project PROJECT_ID] --title TITLE [--parent TASK_ID] [--list LIST_ID] [--description TEXT] [--status icebox|pending|in_progress|in_review|done|will_not_do] [--priority none|low|medium|high|urgent] [--due-at LOCAL_ISO_8601]",
        handler: {:tasks, :create},
        context_role: :project_required,
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID."),
          option(:title, "--title", :string, "TITLE", "Task title.", required?: true),
          option(:parent, "--parent", :positive_integer, "TASK_ID", "Optional parent Task ID."),
          option(:list, "--list", :positive_integer, "LIST_ID", "Optional List ID."),
          option(:description, "--description", :string, "TEXT", "Task description."),
          option(:status, "--status", :string, "STATUS", "Lifecycle status.", values: @statuses),
          option(:priority, "--priority", :string, "PRIORITY", "Task priority.",
            values: @priorities
          ),
          option(:due_at, "--due-at", :string, "LOCAL_ISO_8601", "Local due date and time.")
        ],
        examples: [
          "taskman tasks create --project 7 --title \"Prepare launch\" --status pending"
        ]
      },
      %Command{
        path: ~w(tasks update),
        summary: "Update editable Task fields or lifecycle state.",
        usage:
          "taskman tasks update [--project PROJECT_ID] TASK_ID [--title TITLE] [--description TEXT] [--status icebox|pending|in_progress|in_review|done|will_not_do] [--priority none|low|medium|high|urgent] [--due-at LOCAL_ISO_8601 | --clear-due-at] [--parent PARENT_TASK_ID | --no-parent] [--confirm-unresolved-blockers ID,ID | --force-done-with-unresolved-blockers]",
        handler: {:tasks, :update},
        context_role: :project_required,
        arguments: [argument(:task_id, "TASK_ID", :positive_integer, "Task ID.")],
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID."),
          option(:title, "--title", :string, "TITLE", "Replacement Task title."),
          option(:description, "--description", :string, "TEXT", "Replacement description."),
          option(:status, "--status", :string, "STATUS", "Lifecycle status.", values: @statuses),
          option(:priority, "--priority", :string, "PRIORITY", "Task priority.",
            values: @priorities
          ),
          option(
            :due_at,
            "--due-at",
            :string,
            "LOCAL_ISO_8601",
            "Replacement local due date and time."
          ),
          option(:clear_due_at, "--clear-due-at", :boolean, nil, "Clear the due date."),
          option(
            :parent,
            "--parent",
            :positive_integer,
            "PARENT_TASK_ID",
            "Replacement parent Task ID."
          ),
          option(:no_parent, "--no-parent", :boolean, nil, "Clear the parent Task."),
          option(
            :confirm_unresolved_blockers,
            "--confirm-unresolved-blockers",
            :positive_integer_csv,
            "ID,ID",
            "Confirm the listed unresolved direct blocker IDs for this Done request. Repeated IDs are accepted and deduplicated by the API."
          ),
          option(
            :force_done_with_unresolved_blockers,
            "--force-done-with-unresolved-blockers",
            :boolean,
            nil,
            "Explicitly override the unresolved-blocker warning for this Done request."
          )
        ],
        constraints: [
          {:at_least_one,
           ~w(title description status priority due_at clear_due_at parent no_parent)a},
          {:mutually_exclusive, [:due_at, :clear_due_at]},
          {:mutually_exclusive, [:parent, :no_parent]},
          {:mutually_exclusive,
           [:confirm_unresolved_blockers, :force_done_with_unresolved_blockers]},
          {:requires_value, [:confirm_unresolved_blockers, :force_done_with_unresolved_blockers],
           :status, "done"}
        ],
        examples: [
          "taskman tasks update --project 7 42 --status in_progress",
          "taskman tasks update --project 7 42 --clear-due-at",
          "taskman tasks update --project 7 42 --parent 41",
          "taskman tasks update --project 7 42 --no-parent",
          "taskman tasks update --project 9 42 --status done --confirm-unresolved-blockers 12,15",
          "taskman tasks update --project 9 42 --status done --force-done-with-unresolved-blockers"
        ]
      },
      %Command{
        path: ~w(tasks move),
        summary: "Move a Task within its Project.",
        usage:
          "taskman tasks move [--project PROJECT_ID] TASK_ID (--to-list LIST_ID | --to-project-root)",
        handler: {:tasks, :move},
        context_role: :project_required,
        arguments: [argument(:task_id, "TASK_ID", :positive_integer, "Task ID.")],
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID."),
          option(:to_list, "--to-list", :positive_integer, "LIST_ID", "Destination List ID."),
          option(
            :to_project_root,
            "--to-project-root",
            :boolean,
            nil,
            "Move to the Project root."
          )
        ],
        constraints: [{:exactly_one, [:to_list, :to_project_root]}],
        examples: [
          "taskman tasks move --project 7 42 --to-list 11",
          "taskman tasks move --project 7 42 --to-project-root"
        ]
      },
      %Command{
        path: ~w(config show),
        summary: "Inspect effective host and Project context.",
        usage: "taskman config show",
        handler: {:config, :show},
        context_role: :effective_inspection,
        examples: ["taskman config show --json", "taskman config show --host personal"]
      },
      %Command{
        path: ~w(repo bind),
        summary: "Validate and bind this clone to a host and Project.",
        usage: "taskman repo bind --host NAME --project PROJECT_ID",
        handler: {:repo, :bind},
        context_role: :bind,
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Project ID.",
            required?: true
          )
        ],
        constraints: [{:require_global, [:host]}],
        examples: ["taskman repo bind --host personal --project 7"]
      },
      %Command{
        path: ~w(repo show),
        summary: "Inspect the clone binding offline.",
        usage: "taskman repo show",
        handler: {:repo, :show},
        examples: ["taskman repo show --json"]
      },
      %Command{
        path: ~w(repo unbind),
        summary: "Remove the clone binding offline.",
        usage: "taskman repo unbind",
        handler: {:repo, :unbind},
        examples: ["taskman repo unbind"]
      },
      %Command{
        path: ~w(completions bash),
        summary: "Print Bash completion source.",
        usage: "taskman completions bash",
        handler: {:completions, :bash},
        constraints: [{:forbid_global, [:json]}],
        examples: [
          "taskman completions bash > ~/.local/share/bash-completion/completions/taskman"
        ]
      },
      %Command{
        path: ~w(completions fish),
        summary: "Print Fish completion source.",
        usage: "taskman completions fish",
        handler: {:completions, :fish},
        constraints: [{:forbid_global, [:json]}],
        examples: ["taskman completions fish > ~/.config/fish/completions/taskman.fish"]
      },
      %Command{
        path: ~w(agent onboarding),
        summary: "Print onboarding guidance for people and agents.",
        usage: "taskman agent onboarding",
        handler: {:agent, :onboarding},
        examples: ["taskman agent onboarding", "taskman agent onboarding --json"]
      },
      %Command{
        path: ~w(agent skill install),
        summary: "Install or update the bundled Taskman agent skill.",
        usage: "taskman agent skill install [--force]",
        handler: {:agent, :skill_install},
        options: [option(:force, "--force", :boolean, nil, "Replace an unrecognized target.")],
        examples: ["taskman agent skill install", "taskman agent skill install --force"]
      }
    ] ++ host_commands()
  end

  defp host_commands do
    Enum.map(
      [
        {:host_add, ~w(config hosts add), true, true},
        {:host_list, ~w(config hosts list), false, false},
        {:host_show, ~w(config hosts show), true, false},
        {:host_set_url, ~w(config hosts set-url), true, true},
        {:host_set_key, ~w(config hosts set-key), true, false},
        {:host_remove, ~w(config hosts remove), true, false},
        {:default_host, ~w(config default-host), true, false}
      ],
      fn {action, path, named?, url?} ->
        %Command{
          path: path,
          summary: host_summary(action),
          usage:
            "taskman " <>
              Enum.join(path, " ") <>
              if(named?, do: " NAME", else: "") <> if(url?, do: " --url URL", else: ""),
          handler: {:config, action},
          context_role: :local,
          arguments:
            if(named?,
              do: [argument(:host, "NAME", :host_name, "Registered host name.")],
              else: []
            ),
          options:
            if(url?,
              do: [option(:url, "--url", :string, "URL", "Host base URL.", required?: true)],
              else: []
            ),
          constraints: [{:forbid_global, [:host]}],
          examples: host_examples(action)
        }
      end
    )
  end

  defp host_examples(:host_add),
    do: ["taskman config hosts add personal --url https://taskman.example.com"]

  defp host_examples(:host_list), do: ["taskman config hosts list --json"]
  defp host_examples(:host_show), do: ["taskman config hosts show personal --json"]

  defp host_examples(:host_set_url),
    do: ["taskman config hosts set-url personal --url https://taskman.example.com"]

  defp host_examples(:host_set_key), do: ["taskman config hosts set-key personal"]
  defp host_examples(:host_remove), do: ["taskman config hosts remove personal"]
  defp host_examples(:default_host), do: ["taskman config default-host personal"]

  defp host_summary(:host_add), do: "Register a new host without a key or default selection."

  defp host_summary(:host_list),
    do: "List stored hosts and their credential/default status offline."

  defp host_summary(:host_show), do: "Inspect one stored host without revealing its credential."
  defp host_summary(:host_set_url), do: "Update the address of the same logical backend."
  defp host_summary(:host_set_key), do: "Prompt without echo and save the named host's API key."

  defp host_summary(:host_remove),
    do: "Remove a host and clear its default, retaining clone bindings."

  defp host_summary(:default_host), do: "Select an existing host as the configured default."

  @doc "Global options accepted before or after a command path."
  @spec global_options() :: [Option.t()]
  def global_options do
    [
      option(:host, "--host", :host_name, "NAME", "Registered Taskman host."),
      option(
        :json,
        "--json",
        :boolean,
        nil,
        "Emit one JSON envelope instead of readable output."
      ),
      option(:help, "--help", :boolean, nil, "Show help for the selected command."),
      option(:version, "--version", :boolean, nil, "Print the CLI version.")
    ]
  end

  @doc "Find a command, or identify a known command/group prefix."
  @spec find([String.t()] | String.t()) :: {:ok, Command.t()} | {:group, [String.t()]} | :error
  def find(path) when is_binary(path), do: find(String.split(path, ~r/\s+/, trim: true))

  def find(path) when is_list(path) do
    path = Enum.map(path, &to_string/1)

    case Enum.find(commands(), &(&1.path == path)) do
      %Command{} = command ->
        {:ok, command}

      nil ->
        if path == [] or Enum.any?(commands(), &prefix?(&1.path, path)) do
          {:group, path}
        else
          :error
        end
    end
  end

  def find(_path), do: :error

  defp prefix?(path, prefix) do
    Enum.take(path, length(prefix)) == prefix
  end

  defp option(name, long, type, value_name, help, extra \\ []) do
    %Option{
      name: name,
      long: long,
      type: type,
      value_name: value_name,
      help: help,
      values: Keyword.get(extra, :values, []),
      required?: Keyword.get(extra, :required?, false),
      repeatable?: Keyword.get(extra, :repeatable?, false)
    }
  end

  defp argument(name, value_name, type, help) do
    %Argument{name: name, value_name: value_name, type: type, help: help}
  end
end
