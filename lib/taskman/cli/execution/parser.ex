defmodule Taskman.CLI.Execution.Parser do
  @moduledoc "Parse command-line tokens against the declarative CLI registry."

  alias Taskman.CLI.Execution.Invocation
  alias Taskman.CLI.Registry
  alias Taskman.CLI.Registry.{Command, Option}

  @type parse_result ::
          {:ok, Invocation.t()}
          | {:help, [String.t()]}
          | :version
          | {:error, String.t(), [String.t()]}

  @doc "Parse argv and retain only explicit global CLI options."
  @spec parse([String.t()], map() | keyword()) :: parse_result()
  def parse(argv, env \\ System.get_env())

  def parse(argv, _env) when is_list(argv) do
    case extract_globals(argv) do
      {:error, message, tokens} ->
        {:error, message, usage_path(tokens)}

      {:ok, tokens, cli_api_url, json?, help?, version?} ->
        path = discover_path(tokens)

        cond do
          version? ->
            :version

          help? ->
            {:help, path}

          path == [] and tokens != [] and String.starts_with?(hd(tokens), "--") ->
            {:error, "Unknown option #{hd(tokens)}", []}

          true ->
            parse_command(tokens, path, cli_api_url, json?)
        end
    end
  end

  def parse(_argv, _env), do: {:error, "Arguments must be a list", []}

  defp parse_command(_tokens, [], _cli_api_url, _json?) do
    {:error, "A command is required", []}
  end

  defp parse_command(tokens, path, cli_api_url, json?) do
    case Registry.find(path) do
      {:ok, %Command{} = command} ->
        command_tokens = Enum.drop(tokens, length(path))
        global_options = globals(cli_api_url, json?)

        with {:ok, options, positional} <- parse_tokens(command, command_tokens, path),
             {:ok, arguments} <- parse_arguments(command, positional, path),
             :ok <- validate_required_options(command, options, path),
             :ok <- validate_constraints(command, options, global_options, path) do
          {:ok,
           %Invocation{
             command: command,
             arguments: arguments,
             options: options,
             globals: global_options
           }}
        end

      {:group, _group_path} ->
        {:error, "A leaf command is required", path}

      :error ->
        {:error, "Unknown command: #{Enum.join(path, " ")}", usage_path(path)}
    end
  end

  defp parse_tokens(%Command{} = command, tokens, path) do
    options_by_name = Map.new(command.options, &{normalize_option(&1.long), &1})
    parse_tokens(tokens, options_by_name, %{}, [], path)
  end

  defp parse_tokens([], _options_by_name, options, positional, _path),
    do: {:ok, options, Enum.reverse(positional)}

  defp parse_tokens([token | rest], options_by_name, options, positional, path)
       when is_binary(token) do
    if String.starts_with?(token, "--") do
      {option_name, inline_value} = split_long_option(token)

      case Map.get(options_by_name, option_name) do
        nil ->
          {:error, "Unknown option --#{option_name}", path}

        %Option{type: :boolean} = option ->
          if inline_value != nil do
            {:error, "Option #{option.long} does not take a value", path}
          else
            parse_tokens(
              rest,
              options_by_name,
              Map.put(options, option.name, true),
              positional,
              path
            )
          end

        %Option{} = option ->
          with {:ok, raw_value, remaining} <- option_value(option, inline_value, rest, path),
               {:ok, value} <- cast_option_value(option, raw_value, path) do
            parse_tokens(
              remaining,
              options_by_name,
              put_option(options, option, value),
              positional,
              path
            )
          end
      end
    else
      parse_tokens(rest, options_by_name, options, [token | positional], path)
    end
  end

  defp parse_tokens([token | _rest], _options_by_name, _options, _positional, path) do
    {:error, "Invalid argument #{inspect(token)}", path}
  end

  defp option_value(%Option{} = option, inline_value, rest, path) do
    cond do
      is_binary(inline_value) ->
        {:ok, inline_value, rest}

      rest == [] ->
        {:error, "Missing value for #{option.long}", path}

      hd(rest) |> String.starts_with?("--") ->
        {:error, "Missing value for #{option.long}", path}

      true ->
        {:ok, hd(rest), tl(rest)}
    end
  end

  defp cast_option_value(%Option{type: :string, values: values} = option, value, path) do
    if values == [] or value in values do
      {:ok, value}
    else
      {:error,
       "Invalid value #{inspect(value)} for #{option.long}; expected one of #{Enum.join(values, ", ")}",
       path}
    end
  end

  defp cast_option_value(%Option{type: type} = option, value, path)
       when type in [:integer, :positive_integer] do
    case Integer.parse(value) do
      {integer, ""} when type == :integer -> {:ok, integer}
      {integer, ""} when type == :positive_integer and integer > 0 -> {:ok, integer}
      _ -> {:error, "Invalid integer for #{option.long}: #{inspect(value)}", path}
    end
  end

  defp cast_option_value(%Option{} = option, _value, path) do
    {:error, "Unsupported option type #{inspect(option.type)} for #{option.long}", path}
  end

  defp put_option(options, %Option{name: name, repeatable?: true}, value) do
    Map.update(options, name, [value], &(&1 ++ [value]))
  end

  defp put_option(options, %Option{name: name}, value), do: Map.put(options, name, value)

  defp parse_arguments(%Command{arguments: arguments}, positional, path) do
    if length(arguments) != length(positional) do
      {:error, "Expected #{length(arguments)} argument(s), got #{length(positional)}", path}
    else
      arguments
      |> Enum.zip(positional)
      |> Enum.reduce_while({:ok, %{}}, fn {argument, raw_value}, {:ok, parsed} ->
        case cast_argument(argument.type, raw_value) do
          {:ok, value} ->
            {:cont, {:ok, Map.put(parsed, argument.name, value)}}

          :error ->
            {:halt, {:error, "Invalid #{argument.value_name}: #{inspect(raw_value)}", path}}
        end
      end)
    end
  end

  defp cast_argument(type, value) when type in [:integer, :positive_integer] do
    case Integer.parse(value) do
      {integer, ""} when type == :integer -> {:ok, integer}
      {integer, ""} when type == :positive_integer and integer > 0 -> {:ok, integer}
      _ -> :error
    end
  end

  defp cast_argument(:string, value), do: {:ok, value}
  defp cast_argument(_type, _value), do: :error

  defp validate_required_options(%Command{options: command_options}, options, path) do
    case Enum.find(command_options, &(&1.required? and not Map.has_key?(options, &1.name))) do
      nil -> :ok
      %Option{} = option -> {:error, "Missing required option #{option.long}", path}
    end
  end

  defp validate_constraints(%Command{constraints: constraints}, options, globals, path) do
    Enum.reduce_while(constraints, :ok, fn
      {:at_least_one, fields}, :ok ->
        if Enum.any?(fields, &Map.has_key?(options, &1)) do
          {:cont, :ok}
        else
          {:halt, {:error, "At least one editable option is required", path}}
        end

      {:mutually_exclusive, fields}, :ok ->
        if Enum.count(fields, &Map.has_key?(options, &1)) <= 1 do
          {:cont, :ok}
        else
          {:halt, {:error, "Options #{format_fields(fields)} are mutually exclusive", path}}
        end

      {:exactly_one, fields}, :ok ->
        if Enum.count(fields, &Map.has_key?(options, &1)) == 1 do
          {:cont, :ok}
        else
          {:halt, {:error, "Exactly one of #{format_fields(fields)} is required", path}}
        end

      {:together, fields}, :ok ->
        if Enum.count(fields, &Map.has_key?(options, &1)) in [0, length(fields)] do
          {:cont, :ok}
        else
          {:halt, {:error, "Options #{format_fields(fields)} must be used together", path}}
        end

      {:forbidden_global, field}, :ok ->
        if Map.has_key?(globals, field) do
          {:halt,
           {:error, "Completion output is shell source and cannot be combined with --json", path}}
        else
          {:cont, :ok}
        end

      _constraint, :ok ->
        {:cont, :ok}
    end)
  end

  defp format_fields(fields) do
    fields
    |> Enum.map(&"--#{String.replace(Atom.to_string(&1), "_", "-")}")
    |> Enum.join(", ")
  end

  defp globals(cli_api_url, json?) do
    %{}
    |> maybe_put_global(:api_url, cli_api_url)
    |> maybe_put_global(:json, json?)
  end

  defp maybe_put_global(globals, _key, nil), do: globals
  defp maybe_put_global(globals, _key, false), do: globals
  defp maybe_put_global(globals, key, value), do: Map.put(globals, key, value)

  defp extract_globals(argv), do: extract_globals(argv, [], nil, false, false, false)

  defp extract_globals([], kept, cli_api_url, json?, help?, version?),
    do: {:ok, Enum.reverse(kept), cli_api_url, json?, help?, version?}

  defp extract_globals([token | rest], kept, cli_api_url, json?, help?, version?) do
    cond do
      token in ["--json", "-j"] ->
        extract_globals(rest, kept, cli_api_url, true, help?, version?)

      token in ["--help", "-h"] ->
        extract_globals(rest, kept, cli_api_url, json?, true, version?)

      token in ["--version", "-v"] ->
        extract_globals(rest, kept, cli_api_url, json?, help?, true)

      token == "--api-url" ->
        case rest do
          [value | remaining] when is_binary(value) ->
            if String.starts_with?(value, "--") do
              {:error, "Missing value for --api-url", Enum.reverse(kept)}
            else
              extract_globals(remaining, kept, value, json?, help?, version?)
            end

          _ ->
            {:error, "Missing value for --api-url", Enum.reverse(kept)}
        end

      String.starts_with?(token, "--api-url=") ->
        value = String.replace_prefix(token, "--api-url=", "")

        if value == "" do
          {:error, "Missing value for --api-url", Enum.reverse(kept)}
        else
          extract_globals(rest, kept, value, json?, help?, version?)
        end

      token in [
        "--json=true",
        "--json=false",
        "--help=true",
        "--help=false",
        "--version=true",
        "--version=false"
      ] ->
        {:error, "Global boolean options do not take values", Enum.reverse(kept)}

      true ->
        extract_globals(rest, [token | kept], cli_api_url, json?, help?, version?)
    end
  end

  defp discover_path(tokens) do
    command_paths = Registry.commands() |> Enum.map(& &1.path)

    case Enum.find(command_paths, fn path -> prefix?(tokens, path) end) do
      nil ->
        leading = Enum.take_while(tokens, &(is_binary(&1) and not String.starts_with?(&1, "--")))

        case longest_known_prefix(leading) do
          [] -> leading
          known_path -> Enum.take(leading, length(known_path) + 1)
        end

      path ->
        path
    end
  end

  defp longest_known_prefix(tokens) do
    candidates =
      Registry.commands()
      |> Enum.map(& &1.path)
      |> Enum.flat_map(fn path ->
        for length <- 1..length(path), do: Enum.take(path, length)
      end)
      |> Enum.uniq()
      |> Enum.filter(&prefix?(tokens, &1))

    Enum.max_by(candidates, &length/1, fn -> [] end)
  end

  defp prefix?(tokens, prefix), do: Enum.take(tokens, length(prefix)) == prefix

  defp usage_path([]), do: []
  defp usage_path(path), do: longest_known_prefix(path)

  defp split_long_option(token) do
    case String.split(token, "=", parts: 2) do
      [name] -> {normalize_option(name), nil}
      [name, value] -> {normalize_option(name), value}
    end
  end

  defp normalize_option(name) do
    name
    |> to_string()
    |> String.trim_leading("--")
  end
end
