defmodule Taskman.CLI.Presentation.Help do
  @moduledoc "Render discoverable top-level, group, and leaf command help."

  alias Taskman.CLI.Registry
  alias Taskman.CLI.Registry.{Command, Option}

  @doc "Render help for a command path, a group path, or the top level."
  @spec render([String.t()] | String.t()) :: String.t()
  def render(path) when is_binary(path), do: render(String.split(path, ~r/\s+/, trim: true))

  def render(path) when is_list(path) do
    path = Enum.map(path, &to_string/1)

    case Registry.find(path) do
      {:ok, %Command{} = command} -> render_leaf(command)
      {:group, group_path} -> render_group(group_path)
      :error -> render_unknown(path)
    end
  end

  def render(_path), do: render([])

  defp render_top do
    groups =
      Registry.commands()
      |> Enum.map(&List.first(&1.path))
      |> Enum.uniq()

    utility_commands =
      Registry.commands()
      |> Enum.filter(fn command -> List.first(command.path) in ["completions", "agent"] end)

    lines =
      [
        "Taskman CLI",
        "",
        "Usage: taskman <command> [options]",
        "",
        "Commands:"
      ] ++
        Enum.map(groups, fn group ->
          summary = group_summary(group)
          "  #{String.pad_trailing("taskman #{group}", 26)} #{summary}"
        end) ++
        ["", "Utility commands:"] ++
        Enum.map(utility_commands, fn command ->
          "  #{String.pad_trailing("taskman #{Enum.join(command.path, " ")}", 38)} #{command.summary}"
        end) ++
        [
          "",
          "Global options:"
        ] ++
        option_lines(Registry.global_options()) ++
        [
          "",
          "Configuration:",
          "  Host selection: --host NAME, repository binding, TASKMAN_HOST, configured default.",
          "  Settings are read from ${XDG_CONFIG_HOME:-$HOME/.config}/taskman/config.json.",
          "  Fresh configuration has no implicit host or URL; register one with config hosts add.",
          "  Pair TASKMAN_API_KEY with TASKMAN_API_KEY_HOST matching the selected host.",
          "  Local host management and repo show/unbind work offline.",
          "  Use taskman agent onboarding for setup, legacy upgrade, and clone recovery.",
          "  Global options may appear before or after the command path.",
          "",
          "Use taskman <group> --help or taskman <group> <command> --help for details."
        ]

    Enum.join(lines, "\n") <> "\n"
  end

  defp render_group([]), do: render_top()

  defp render_group(path) do
    children =
      Registry.commands()
      |> Enum.filter(&prefix?(&1.path, path))
      |> Enum.filter(&(length(&1.path) > length(path)))

    lines =
      [
        "Taskman #{Enum.join(path, " ")}",
        "",
        "Usage: taskman #{Enum.join(path, " ")} <command> [options]",
        "",
        "Commands:"
      ] ++
        Enum.map(children, fn command ->
          label = "taskman #{Enum.join(command.path, " ")}"
          "  #{String.pad_trailing(label, 38)} #{command.summary}"
        end) ++
        [
          "",
          "Global options are accepted before or after the command path.",
          "Use taskman #{Enum.join(path, " ")} <command> --help for details."
        ]

    Enum.join(lines, "\n") <> "\n"
  end

  defp render_leaf(%Command{} = command) do
    lines = [
      "Taskman #{Enum.join(command.path, " ")}",
      "",
      command.summary,
      "",
      "Usage:",
      "  #{command.usage}"
    ]

    lines =
      if command.arguments == [] do
        lines
      else
        lines ++ ["", "Arguments:"] ++ Enum.map(command.arguments, &argument_line/1)
      end

    lines =
      if command.options == [] do
        lines
      else
        lines ++ ["", "Options:"] ++ option_lines(command.options)
      end

    lines =
      lines ++
        context_lines(command) ++
        output_lines(command) ++
        [
          "",
          "Exit statuses:",
          "  0 success; 2 invalid invocation/configuration; 3 API/domain error; 4 connection failure; 5 internal/contract error; 6 skill installation failure; 7 authentication required, rejected, or forbidden.",
          "",
          "Examples:"
        ] ++ Enum.map(command.examples, &"  #{&1}")

    Enum.join(lines, "\n") <> "\n"
  end

  defp render_unknown(path) do
    name = if path == [], do: "", else: " #{Enum.join(path, " ")}"

    "Unknown command#{name}.\n\n" <> render_top()
  end

  defp group_summary("projects"), do: "Manage Projects"
  defp group_summary("lists"), do: "Manage Lists"
  defp group_summary("tasks"), do: "Manage Tasks"
  defp group_summary("repo"), do: "Manage shared clone bindings"
  defp group_summary("config"), do: "Manage CLI API configuration"
  defp group_summary("completions"), do: "Generate shell completions"
  defp group_summary("agent"), do: "Agent onboarding and skill tools"
  defp group_summary(_group), do: "Command group"

  defp argument_line(argument) do
    "  #{argument.value_name}\t#{argument.help}"
  end

  defp option_lines(options) do
    Enum.map(options, fn %Option{} = option ->
      value = if option.value_name, do: " #{option.value_name}", else: ""
      values = if option.values == [], do: "", else: " (#{Enum.join(option.values, "|")})"
      required = if option.required?, do: " (required)", else: ""
      repeatable = if option.repeatable?, do: " (repeatable)", else: ""
      "  #{option.long}#{value}#{values}#{required}#{repeatable}\t#{option.help}"
    end)
  end

  defp context_lines(%Command{context_role: role})
       when role in [:project_required, :project_filter] do
    common = [
      "",
      "Context:",
      "  The bound Project is used when no explicit scope is supplied; linked worktrees share it.",
      "  To override a bound Project, supply both --host and --project, even for matching values.",
      "  Examples with only --project assume unbound context; add --host NAME for bound overrides."
    ]

    case role do
      :project_required ->
        common ++
          [
            "  Unbound commands require --project; select a registered host or configure a default."
          ]

      :project_filter ->
        common ++
          [
            "  Unbound search spans all Projects unless --project is supplied.",
            "  --all-projects searches the selected host and conflicts with --project.",
            "  --host NAME --all-projects permits a host-only search override."
          ]
    end
  end

  defp context_lines(%Command{context_role: :bind}),
    do: [
      "",
      "Context:",
      "  Requires both selectors and validates the authenticated Project before saving.",
      "  The clone binding is shared by linked worktrees; credentials stay in user configuration."
    ]

  defp context_lines(%Command{handler: {:config, :host_set_url}}),
    do: [
      "",
      "Context:",
      "  Change the address of the same logical backend; a different backend needs a new name.",
      "  All repositories bound to this host use the updated URL. API key retained."
    ]

  defp context_lines(%Command{handler: {:config, action}})
       when action in [
              :host_add,
              :host_list,
              :host_show,
              :host_set_key,
              :host_remove,
              :default_host
            ],
       do: [
         "",
         "Context:",
         "  Management targets are named operands; --host is invalid here.",
         "  These commands ignore transport-selection environment variables for offline recovery."
       ]

  defp context_lines(%Command{handler: {:repo, :show}}),
    do: [
      "",
      "Context:",
      "  Reports dangling host references offline; it does not validate backend or Project identity."
    ]

  defp context_lines(%Command{handler: {:repo, :unbind}}),
    do: [
      "",
      "Context:",
      "  Offline and idempotent; removes malformed bindings too. Inspect with repo show, then bind again."
    ]

  defp context_lines(_command), do: []

  defp output_lines(%Command{} = command) do
    output_description =
      cond do
        Command.forbidden_global?(command, :json) ->
          "  This command writes shell source to stdout; --json is invalid."

        local_store_command?(command) ->
          "  This command operates on local state offline and does not contact the backend."

        true ->
          "  Successful data is readable by default; add --json for one API-compatible JSON envelope."
      end

    search_details =
      if command.handler == {:tasks, :search} do
        ["  Readable summaries use ID, TITLE, STATUS, PRIORITY, PROJECT, and LOCATION columns."]
      else
        []
      end

    [
      "",
      "Output:",
      output_description
    ] ++
      search_details ++
      [
        "  Diagnostics and failed operations are written to stderr."
      ]
  end

  defp local_store_command?(%Command{handler: {:config, _action}}), do: true

  defp local_store_command?(%Command{handler: {:repo, action}}) when action in [:show, :unbind],
    do: true

  defp local_store_command?(_command), do: false

  defp prefix?(path, prefix), do: Enum.take(path, length(prefix)) == prefix
end
