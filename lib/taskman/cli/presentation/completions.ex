defmodule Taskman.CLI.Presentation.Completions do
  @moduledoc "Generate offline Bash and Fish completion scripts from the CLI registry."

  alias Taskman.CLI.Registry

  @doc "Render the Bash completion script for the taskman command."
  @spec bash() :: String.t()
  def bash do
    commands = Registry.commands()
    global_options = Registry.global_options()
    value_options = value_options(commands, global_options)

    [
      "# Bash completion for taskman, generated from Taskman.CLI.Registry.",
      "_taskman_option_present() {",
      "  local option=\"$1\" token i",
      "  for ((i = 1; i < COMP_CWORD; i++)); do",
      "    token=\"${COMP_WORDS[i]}\"",
      "    case \"$token\" in",
      "      \"$option\"|\"$option\"=*) return 0 ;;",
      "    esac",
      "  done",
      "  return 1",
      "}",
      "",
      "_taskman() {",
      "  local cur=\"${COMP_WORDS[COMP_CWORD]}\"",
      "  local prev=\"\"",
      "  if (( COMP_CWORD > 0 )); then prev=\"${COMP_WORDS[COMP_CWORD - 1]}\"; fi",
      "  local i token skip_value=0",
      "  local -a command_words=()",
      "",
      "  # Ignore option values while finding the registry command path.",
      "  for ((i = 1; i < COMP_CWORD; i++)); do",
      "    token=\"${COMP_WORDS[i]}\"",
      "    if (( skip_value )); then",
      "      skip_value=0",
      "      continue",
      "    fi",
      "    case \"$token\" in",
      "#{bash_value_option_patterns(value_options)}",
      "      --*=*) continue ;;",
      "      --*) continue ;;",
      "      *) command_words+=(\"$token\") ;;",
      "    esac",
      "  done",
      "",
      "  local command_words_text=\"${command_words[*]}\"",
      "  local command_path=\"\"",
      "  case \"$command_words_text\" in",
      bash_command_path_cases(commands),
      "    *) command_path=\"\" ;;",
      "  esac",
      "",
      "  case \"$cur\" in",
      bash_inline_value_cases(value_options, commands),
      "  esac",
      "",
      "  case \"$prev\" in",
      bash_value_cases(value_options, commands),
      "  esac",
      "",
      "  local suggestions=\"\"",
      "  case \"$command_path\" in",
      bash_suggestion_cases(commands, global_options),
      "    *) suggestions=\"\" ;;",
      "  esac",
      "",
      "  # Honor mutually-exclusive and exactly-one Registry constraints.",
      bash_constraint_lines(commands),
      "",
      "  COMPREPLY=( $(compgen -W \"$suggestions\" -- \"$cur\") )",
      "}",
      "",
      "complete -F _taskman taskman",
      "",
      bash_command_comments(commands),
      ""
    ]
    |> Enum.join("\n")
  end

  @doc "Render the Fish completion script for the taskman command."
  @spec fish() :: String.t()
  def fish do
    fish(Registry.commands())
  end

  @doc false
  def fish(commands) when is_list(commands) do
    global_options = Registry.global_options()

    [
      "# Fish completion for taskman, generated from Taskman.CLI.Registry.",
      "complete -c taskman -f",
      "",
      "# Global options are valid before or after every command path.",
      fish_command_path_helper(commands, global_options),
      fish_global_option_helpers(global_options, commands),
      fish_global_option_lines(global_options, commands),
      "",
      "# Registry command paths and their valid child suggestions.",
      "complete -c taskman -f -n #{fish_quote(fish_path_condition([]))} -a " <>
        fish_quote(top_level_paths(commands)),
      fish_group_lines(commands),
      "",
      fish_command_option_lines(commands),
      "",
      fish_option_comments(global_options ++ Enum.flat_map(commands, & &1.options)),
      "",
      fish_command_comments(commands),
      ""
    ]
    |> List.flatten()
    |> Enum.join("\n")
  end

  defp value_options(commands, global_options) do
    (global_options ++ Enum.flat_map(commands, & &1.options))
    |> Enum.filter(&(&1.value_name != nil))
    |> Enum.uniq_by(& &1.long)
  end

  defp bash_value_option_patterns(options) do
    Enum.map_join(options, "\n", fn option ->
      "      #{option.long}) skip_value=1; continue ;;"
    end)
  end

  defp bash_command_path_cases(commands) do
    commands
    |> command_path_prefixes()
    |> Enum.sort_by(&(-length(String.split(&1, " "))))
    |> Enum.map_join("\n", fn path ->
      "    #{bash_case_pattern(path)}) command_path=#{bash_quote(path)} ;;"
    end)
  end

  defp bash_value_cases(options, commands) do
    Enum.map_join(options, "\n", fn option ->
      case option.values do
        [] ->
          "    #{option.long}) COMPREPLY=(); return ;;"

        values ->
          paths = option_command_paths(option, commands)
          path_patterns = Enum.map_join(paths, "|", &bash_case_pattern/1)

          "    #{option.long})\n" <>
            "      case \"$command_path\" in\n" <>
            "        #{path_patterns}) COMPREPLY=( $(compgen -W #{bash_quote(Enum.join(values, " "))} -- \"$cur\") ); return ;;\n" <>
            "      esac\n" <>
            "      COMPREPLY=(); return\n" <>
            "      ;;"
      end
    end)
    |> String.trim_trailing()
  end

  defp bash_inline_value_cases(options, commands) do
    Enum.map_join(options, "\n", fn option ->
      case option.values do
        [] ->
          "    #{option.long}=*) COMPREPLY=(); return ;;"

        values ->
          value_completion =
            "COMPREPLY=( $(compgen -P #{bash_quote(option.long <> "=")} -W #{bash_quote(Enum.join(values, " "))} -- \"$value_prefix\") ); return"

          case option_command_paths(option, commands) do
            [] ->
              "    #{option.long}=*)\n" <>
                "      local value_prefix=\"${cur#*=}\"\n" <>
                "      #{value_completion}\n" <>
                "      ;;"

            paths ->
              path_patterns = Enum.map_join(paths, "|", &bash_case_pattern/1)

              "    #{option.long}=*)\n" <>
                "      local value_prefix=\"${cur#*=}\"\n" <>
                "      case \"$command_path\" in\n" <>
                "        #{path_patterns}) #{value_completion} ;;\n" <>
                "      esac\n" <>
                "      COMPREPLY=(); return\n" <>
                "      ;;"
          end
      end
    end)
  end

  defp option_command_paths(option, commands) do
    commands
    |> Enum.filter(fn command -> Enum.any?(command.options, &(&1.long == option.long)) end)
    |> Enum.map(&Enum.join(&1.path, " "))
  end

  defp bash_constraint_lines(commands) do
    commands
    |> Enum.flat_map(fn command ->
      command
      |> constraint_groups()
      |> Enum.flat_map(&bash_constraint_lines(command, &1))
    end)
    |> Enum.join("\n")
  end

  defp bash_constraint_lines(command, fields) do
    fields
    |> Enum.map(fn selected ->
      selected_option = option_for_name(command, selected)

      other_options =
        fields
        |> Enum.reject(&(&1 == selected))
        |> Enum.map(&option_for_name(command, &1))
        |> Enum.reject(&is_nil/1)

      if selected_option && other_options != [] do
        other_removals =
          Enum.map_join(other_options, "\n", fn option ->
            "      suggestions=\"${suggestions//#{option.long}/}\""
          end)

        [
          "  if [[ \"$command_path\" == #{bash_quote(Enum.join(command.path, " "))} ]]; then",
          "    if _taskman_option_present #{bash_quote(selected_option.long)}; then",
          other_removals,
          "    fi",
          "  fi"
        ]
        |> Enum.join("\n")
      else
        nil
      end
    end)
    |> Enum.reject(&is_nil/1)
  end

  defp constraint_groups(command) do
    command.constraints
    |> Enum.flat_map(fn
      {kind, fields} when kind in [:mutually_exclusive, :exactly_one] -> [fields]
      _constraint -> []
    end)
    |> Enum.uniq()
  end

  defp option_for_name(command, name), do: Enum.find(command.options, &(&1.name == name))

  defp prefix_global_options(prefix, commands, global_options) do
    descendants = descendant_commands(commands, prefix)

    Enum.filter(global_options, fn option ->
      Enum.any?(descendants, &(not forbidden_global?(&1, option.name)))
    end)
  end

  defp descendant_commands(commands, prefix) do
    prefix_tokens = String.split(prefix, " ", trim: true)

    Enum.filter(commands, fn command ->
      Enum.take(command.path, length(prefix_tokens)) == prefix_tokens
    end)
  end

  defp bash_suggestion_cases(commands, global_options) do
    globals = option_words(global_options)
    root_suggestions = Enum.join(top_level_path_names(commands) ++ globals, " ")

    prefix_cases =
      command_prefixes_with_children(commands)
      |> Enum.map(fn {prefix, children} ->
        prefix_globals = prefix_global_options(prefix, commands, global_options)
        suggestions = Enum.join(children ++ option_words(prefix_globals), " ")
        "    #{bash_quote(prefix)}) suggestions=#{bash_quote(suggestions)} ;;"
      end)

    command_cases =
      Enum.map(commands, fn command ->
        options =
          option_words(command.options) ++
            option_words(allowed_global_options(command, global_options))

        "    #{bash_quote(Enum.join(command.path, " "))}) suggestions=#{bash_quote(Enum.join(options, " "))} ;;"
      end)

    Enum.join(
      ["    '') suggestions=#{bash_quote(root_suggestions)} ;;" | prefix_cases ++ command_cases],
      "\n"
    )
  end

  defp bash_command_comments(commands) do
    Enum.map_join(commands, "\n", fn command ->
      "# taskman #{Enum.join(command.path, " ")}"
    end)
  end

  defp fish_option_lines(options, condition) do
    Enum.map(options, fn option ->
      condition = if condition, do: " -n #{fish_quote(condition)}", else: ""
      requires_value = if option.value_name, do: " -r", else: ""

      values =
        if option.values == [], do: "", else: " -a #{fish_quote(Enum.join(option.values, " "))}"

      "complete -c taskman -f#{condition} -l #{String.trim_leading(option.long, "--")}#{requires_value}#{values}"
    end)
  end

  defp fish_global_option_lines(options, commands) do
    Enum.flat_map(options, fn option ->
      condition = forbidden_global_condition(option, commands)
      fish_option_lines([option], condition)
    end)
  end

  defp fish_command_path_helper(commands, global_options) do
    value_patterns = fish_value_option_patterns(value_options(commands, global_options))
    known_command_paths = command_path_prefixes(commands)

    known_paths =
      known_command_paths
      |> Enum.map(&fish_quote/1)
      |> Enum.join(" ")

    [
      "function __taskman_command_path",
      "  set -l tokens (commandline -opc)",
      "  set -l command_words",
      "  set -l skip_value 0",
      "  set -l path_complete 0",
      "  set -l known_command_paths #{known_paths}",
      "  for token in $tokens[2..-1]",
      "    if test $skip_value -eq 1",
      "      set skip_value 0",
      "      continue",
      "    end",
      "    switch $token",
      value_patterns,
      "      case '--*=*'",
      "        continue",
      "      case '--*'",
      "        continue",
      "      case '*'",
      "        if test $path_complete -eq 1",
      "          continue",
      "        end",
      "        set -l candidate (string join ' ' -- $command_words $token)",
      "        if contains -- \"$candidate\" $known_command_paths",
      "          set --append command_words $token",
      "        else",
      "          set path_complete 1",
      "        end",
      "    end",
      "  end",
      "  string join ' ' -- $command_words",
      "end",
      "",
      "function __taskman_command_path_matches",
      "  set -l expected \"$argv[1]\"",
      "  set -l actual (__taskman_command_path)",
      "  test \"$actual\" = \"$expected\"",
      "end",
      ""
    ]
  end

  defp fish_value_option_patterns(options) do
    Enum.flat_map(options, fn option ->
      ["      case #{option.long}", "        set skip_value 1", "        continue"]
    end)
  end

  defp fish_global_option_helpers(options, commands) do
    Enum.flat_map(options, fn option ->
      case forbidden_global_prefixes(option, commands) do
        [] ->
          []

        prefixes ->
          ["function #{fish_global_option_function(option)}"] ++
            ["  set -l command_path (__taskman_command_path)"] ++
            Enum.flat_map(prefixes, &fish_forbidden_prefix_lines/1) ++
            ["  return 0", "end", ""]
      end
    end)
  end

  defp forbidden_global_condition(option, commands) do
    if forbidden_global_prefixes(option, commands) == [],
      do: nil,
      else: fish_global_option_function(option)
  end

  defp forbidden_global_prefixes(option, commands) do
    all_prefixes =
      commands
      |> Enum.flat_map(fn command ->
        for length <- 1..length(command.path) do
          Enum.take(command.path, length)
        end
      end)
      |> Enum.uniq()
      |> Enum.filter(fn prefix ->
        commands
        |> Enum.filter(fn command -> Enum.take(command.path, length(prefix)) == prefix end)
        |> Enum.all?(&forbidden_global?(&1, option.name))
      end)
      |> Enum.sort_by(&length/1)

    Enum.reject(all_prefixes, fn prefix ->
      Enum.any?(all_prefixes, fn parent ->
        length(parent) < length(prefix) and Enum.take(prefix, length(parent)) == parent
      end)
    end)
  end

  defp fish_forbidden_prefix_lines(prefix) do
    pattern = Enum.join(prefix, " ") <> "*"

    [
      "  if string match -q -- #{fish_quote(pattern)} \"$command_path\"",
      "    return 1",
      "  end"
    ]
  end

  defp fish_global_option_function(option), do: "__taskman_global_#{option.name}"

  defp forbidden_global?(command, option_name) do
    Enum.any?(command.constraints, fn
      {:forbidden_global, ^option_name} -> true
      _constraint -> false
    end)
  end

  defp allowed_global_options(command, global_options) do
    forbidden =
      command.constraints
      |> Enum.flat_map(fn
        {:forbidden_global, field} -> [field]
        _constraint -> []
      end)

    Enum.reject(global_options, &(&1.name in forbidden))
  end

  defp fish_group_lines(commands) do
    command_prefixes_with_children(commands)
    |> Enum.map(fn {prefix, children} ->
      condition = fish_prefix_condition(prefix)

      "complete -c taskman -f -n #{fish_quote(condition)} -a #{fish_quote(Enum.join(children, " "))}"
    end)
  end

  defp fish_command_option_lines(commands) do
    Enum.flat_map(commands, fn command ->
      Enum.flat_map(command.options, fn option ->
        condition = fish_command_option_condition(command, option)
        fish_option_lines([option], condition)
      end)
    end)
  end

  defp fish_command_option_condition(command, option) do
    constraint_conditions =
      command
      |> constraint_groups()
      |> Enum.flat_map(fn fields ->
        if option.name in fields do
          fields
          |> Enum.reject(&(&1 == option.name))
          |> Enum.map(&option_for_name(command, &1))
          |> Enum.reject(&is_nil/1)
          |> Enum.map(fn other ->
            "not __fish_seen_argument --long #{String.trim_leading(other.long, "--")}"
          end)
        else
          []
        end
      end)

    join_fish_conditions([fish_command_condition(command) | constraint_conditions])
  end

  defp fish_command_comments(commands) do
    Enum.map(commands, fn command -> "# taskman #{Enum.join(command.path, " ")}" end)
  end

  defp fish_option_comments(options) do
    options
    |> Enum.uniq_by(& &1.long)
    |> Enum.map(fn option -> "# registry option #{option.long}" end)
  end

  defp fish_path_condition(path) when is_list(path),
    do: fish_path_condition(Enum.join(path, " "))

  defp fish_path_condition(path) when is_binary(path),
    do: "__taskman_command_path_matches #{fish_quote(path)}"

  defp fish_prefix_condition(prefix), do: fish_path_condition(prefix)

  defp fish_command_condition(command), do: fish_path_condition(command.path)

  defp join_fish_conditions(conditions) do
    conditions
    |> Enum.reject(&(&1 == ""))
    |> Enum.join("; and ")
  end

  defp top_level_paths(commands) do
    commands
    |> Enum.map(&List.first(&1.path))
    |> Enum.uniq()
    |> Enum.join(" ")
  end

  defp top_level_path_names(commands) do
    commands
    |> Enum.map(&List.first(&1.path))
    |> Enum.uniq()
  end

  defp command_path_prefixes(commands) do
    commands
    |> Enum.flat_map(fn command ->
      for length <- 1..length(command.path) do
        command.path |> Enum.take(length) |> Enum.join(" ")
      end
    end)
    |> Enum.uniq()
  end

  defp command_prefixes_with_children(commands) do
    command_path_prefixes(commands)
    |> Enum.map(fn prefix -> {prefix, command_children(commands, prefix)} end)
    |> Enum.reject(fn {_prefix, children} -> children == [] end)
  end

  defp command_children(commands, prefix) do
    prefix_tokens = String.split(prefix, " ", trim: true)

    commands
    |> Enum.map(& &1.path)
    |> Enum.filter(fn path ->
      length(path) > length(prefix_tokens) and
        Enum.take(path, length(prefix_tokens)) == prefix_tokens
    end)
    |> Enum.map(&Enum.at(&1, length(prefix_tokens)))
    |> Enum.uniq()
  end

  defp option_words(options), do: Enum.map(options, & &1.long)

  defp bash_case_pattern(path), do: bash_quote(path) <> "*"

  defp bash_quote(value) do
    "'" <> String.replace(to_string(value), "'", "'\\''") <> "'"
  end

  defp fish_quote(value) do
    "'" <> String.replace(to_string(value), "'", "\\'") <> "'"
  end
end
