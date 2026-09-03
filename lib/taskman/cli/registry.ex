defmodule Taskman.CLI.Registry do
  @moduledoc "The single declarative source of truth for the Taskman CLI."

  alias Taskman.CLI.{Argument, Command, Option}

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
        examples: ["taskman projects list", "taskman projects list --json"]
      },
      %Command{
        path: ~w(projects show),
        summary: "Inspect one Project by ID.",
        usage: "taskman projects show PROJECT_ID",
        handler: {:projects, :show},
        arguments: [argument(:project_id, "PROJECT_ID", :positive_integer, "Project ID.")],
        examples: ["taskman projects show 7"]
      },
      %Command{
        path: ~w(projects create),
        summary: "Create a Project.",
        usage: "taskman projects create --name NAME --directory PATH",
        handler: {:projects, :create},
        options: [
          option(:name, "--name", :string, "NAME", "Project name.", required?: true),
          option(:directory, "--directory", :string, "PATH", "Primary directory.",
            required?: true
          )
        ],
        examples: ["taskman projects create --name CLI --directory /work/project"]
      },
      %Command{
        path: ~w(lists list),
        summary: "List a Project's Lists in tree order.",
        usage: "taskman lists list --project PROJECT_ID",
        handler: {:lists, :list},
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID.",
            required?: true
          )
        ],
        examples: ["taskman lists list --project 7"]
      },
      %Command{
        path: ~w(lists show),
        summary: "Inspect one List by ID.",
        usage: "taskman lists show --project PROJECT_ID LIST_ID",
        handler: {:lists, :show},
        arguments: [argument(:list_id, "LIST_ID", :positive_integer, "List ID.")],
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID.",
            required?: true
          )
        ],
        examples: ["taskman lists show --project 7 11"]
      },
      %Command{
        path: ~w(lists create),
        summary: "Create a root or child List.",
        usage: "taskman lists create --project PROJECT_ID --name NAME [--parent LIST_ID]",
        handler: {:lists, :create},
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID.",
            required?: true
          ),
          option(:name, "--name", :string, "NAME", "List name.", required?: true),
          option(:parent, "--parent", :positive_integer, "LIST_ID", "Optional parent List ID.")
        ],
        examples: ["taskman lists create --project 7 --name Launch --parent 11"]
      },
      %Command{
        path: ~w(lists rename),
        summary: "Rename a List.",
        usage: "taskman lists rename --project PROJECT_ID LIST_ID --name NAME",
        handler: {:lists, :rename},
        arguments: [argument(:list_id, "LIST_ID", :positive_integer, "List ID.")],
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID.",
            required?: true
          ),
          option(:name, "--name", :string, "NAME", "New List name.", required?: true)
        ],
        examples: ["taskman lists rename --project 7 11 --name Planning"]
      },
      %Command{
        path: ~w(tasks list),
        summary: "List Tasks for a Project or List.",
        usage:
          "taskman tasks list --project PROJECT_ID [--list LIST_ID] [--include-descendants] [--status STATUS]... [--sort FIELD --direction asc|desc]",
        handler: {:tasks, :list},
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID.",
            required?: true
          ),
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
        path: ~w(tasks show),
        summary: "Inspect one Task by ID.",
        usage: "taskman tasks show --project PROJECT_ID TASK_ID",
        handler: {:tasks, :show},
        arguments: [argument(:task_id, "TASK_ID", :positive_integer, "Task ID.")],
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID.",
            required?: true
          )
        ],
        examples: ["taskman tasks show --project 7 42"]
      },
      %Command{
        path: ~w(tasks hierarchy),
        summary: "Inspect a Task's connected hierarchy.",
        usage: "taskman tasks hierarchy --project PROJECT_ID TASK_ID",
        handler: {:tasks, :hierarchy},
        arguments: [argument(:task_id, "TASK_ID", :positive_integer, "Task ID.")],
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID.",
            required?: true
          )
        ],
        examples: ["taskman tasks hierarchy --project 7 42"]
      },
      %Command{
        path: ~w(tasks create),
        summary: "Create a Task.",
        usage:
          "taskman tasks create --project PROJECT_ID --title TITLE [--parent TASK_ID] [--list LIST_ID] [--description TEXT] [--status icebox|pending|in_progress|in_review|done|will_not_do] [--priority none|low|medium|high|urgent] [--due-at LOCAL_ISO_8601]",
        handler: {:tasks, :create},
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID.",
            required?: true
          ),
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
          "taskman tasks update --project PROJECT_ID TASK_ID [--title TITLE] [--description TEXT] [--status icebox|pending|in_progress|in_review|done|will_not_do] [--priority none|low|medium|high|urgent] [--due-at LOCAL_ISO_8601 | --clear-due-at] [--parent PARENT_TASK_ID | --no-parent]",
        handler: {:tasks, :update},
        arguments: [argument(:task_id, "TASK_ID", :positive_integer, "Task ID.")],
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID.",
            required?: true
          ),
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
          option(:no_parent, "--no-parent", :boolean, nil, "Clear the parent Task.")
        ],
        constraints: [
          {:at_least_one,
           ~w(title description status priority due_at clear_due_at parent no_parent)a},
          {:mutually_exclusive, [:due_at, :clear_due_at]},
          {:mutually_exclusive, [:parent, :no_parent]}
        ],
        examples: [
          "taskman tasks update --project 7 42 --status in_progress",
          "taskman tasks update --project 7 42 --clear-due-at",
          "taskman tasks update --project 7 42 --parent 41",
          "taskman tasks update --project 7 42 --no-parent"
        ]
      },
      %Command{
        path: ~w(tasks move),
        summary: "Move a Task within its Project.",
        usage:
          "taskman tasks move --project PROJECT_ID TASK_ID (--to-list LIST_ID | --to-project-root)",
        handler: {:tasks, :move},
        arguments: [argument(:task_id, "TASK_ID", :positive_integer, "Task ID.")],
        options: [
          option(:project, "--project", :positive_integer, "PROJECT_ID", "Owning Project ID.",
            required?: true
          ),
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
        path: ~w(config set-url),
        summary: "Store the Taskman API base URL.",
        usage: "taskman config set-url URL",
        handler: {:config, :set_url},
        arguments: [argument(:api_url, "URL", :string, "HTTP or HTTPS Taskman API base URL.")],
        examples: ["taskman config set-url https://taskman.example.com"]
      },
      %Command{
        path: ~w(config set-key),
        summary: "Prompt for and store a Taskman API key.",
        usage: "taskman config set-key",
        handler: {:config, :set_key},
        examples: ["taskman config set-key"]
      },
      %Command{
        path: ~w(config show),
        summary: "Show the resolved Taskman API configuration without revealing its key.",
        usage: "taskman config show",
        handler: {:config, :show},
        examples: ["taskman config show", "taskman config show --json"]
      },
      %Command{
        path: ~w(completions bash),
        summary: "Print Bash completion source.",
        usage: "taskman completions bash",
        handler: {:completions, :bash},
        constraints: [{:forbidden_global, :json}],
        examples: [
          "taskman completions bash > ~/.local/share/bash-completion/completions/taskman"
        ]
      },
      %Command{
        path: ~w(completions fish),
        summary: "Print Fish completion source.",
        usage: "taskman completions fish",
        handler: {:completions, :fish},
        constraints: [{:forbidden_global, :json}],
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
    ]
  end

  @doc "Global options accepted before or after a command path."
  @spec global_options() :: [Option.t()]
  def global_options do
    [
      option(:api_url, "--api-url", :string, "URL", "Taskman API base URL."),
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
