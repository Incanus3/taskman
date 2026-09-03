defmodule Taskman.CLI do
  @moduledoc "Offline-safe escript entry point for the Taskman command line."

  alias Taskman.CLI.{Help, Parser, Result, Runner}

  @version "0.2.0"

  @doc "Return the version bundled in the escript."
  @spec version() :: String.t()
  def version, do: @version

  @doc "Parse and run args, returning a result without writing to the process IO."
  @spec run([String.t()], keyword() | map()) :: Result.t()
  def run(args, runtime_options \\ []) do
    env = runtime_env(runtime_options)

    case Parser.parse(args, env) do
      {:ok, invocation} -> Runner.run(invocation, runtime_options)
      {:help, path} -> %Result{stdout: Help.render(path)}
      :version -> %Result{stdout: version() <> "\n"}
      {:error, message, path} -> invalid_result(message, path, json_requested?(args))
    end
  end

  @doc "The escript main callback; this is the only process-exit boundary."
  @spec main([String.t()]) :: no_return()
  def main(args) do
    result = run(args)
    if result.stdout != "", do: IO.write(:stdio, result.stdout)
    if result.stderr != "", do: IO.write(:stderr, result.stderr)
    System.halt(result.status)
  end

  defp invalid_result(message, _path, true) do
    %Result{
      status: 2,
      stderr:
        Jason.encode!(%{
          error: %{code: "invalid_invocation", message: message}
        }) <> "\n"
    }
  end

  defp invalid_result(message, path, false) do
    usage = Help.render(path)
    %Result{status: 2, stderr: "Invalid invocation: #{message}.\n\n#{usage}"}
  end

  defp json_requested?(args) when is_list(args), do: "--json" in args or "-j" in args
  defp json_requested?(_args), do: false

  defp runtime_env(options) when is_list(options),
    do: Keyword.get(options, :env, System.get_env())

  defp runtime_env(options) when is_map(options), do: Map.get(options, :env, options)
  defp runtime_env(_options), do: System.get_env()
end
