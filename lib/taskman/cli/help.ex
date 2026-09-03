defmodule Taskman.CLI.Help do
  @moduledoc "Render discoverable top-level, group, and leaf command help."

  alias Taskman.CLI.{Command, Option, Registry}

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
          "  --api-url takes precedence over TASKMAN_API_URL; TASKMAN_API_KEY overrides config.json.",
          "  Settings are read from ${XDG_CONFIG_HOME:-$HOME/.config}/taskman/config.json.",
          "  The URL falls back to http://localhost:4000; ordinary API commands require an API key.",
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

  defp output_lines(%Command{} = command) do
    output_description =
      cond do
        forbidden_global?(command, :json) ->
          "  This command writes shell source to stdout; --json is invalid."

        config_command?(command) ->
          "  This command operates on local configuration and does not contact the backend."

        true ->
          "  Successful data is readable by default; add --json for one API-compatible JSON envelope."
      end

    [
      "",
      "Output:",
      output_description,
      "  Diagnostics and failed operations are written to stderr."
    ]
  end

  defp forbidden_global?(%Command{constraints: constraints}, option_name) do
    Enum.any?(constraints, &match?({:forbidden_global, ^option_name}, &1))
  end

  defp config_command?(%Command{handler: {:config, _action}}), do: true
  defp config_command?(_command), do: false

  defp prefix?(path, prefix), do: Enum.take(path, length(prefix)) == prefix
end
