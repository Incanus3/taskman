defmodule Taskman.CLI.Execution.Runner do
  @moduledoc "Dispatch parsed invocations while keeping IO at the CLI boundary."

  alias Taskman.CLI.{Config, Onboarding}
  alias Taskman.CLI.Commands.Config, as: ConfigCommand
  alias Taskman.CLI.Commands.Lists
  alias Taskman.CLI.Commands.Projects
  alias Taskman.CLI.Commands.Tasks
  alias Taskman.CLI.Execution.{Invocation, Result}
  alias Taskman.CLI.Presentation.{Completions, Output}
  alias Taskman.CLI.Registry.Command
  alias Taskman.CLI.Skill.Installer

  @doc "Run a parsed invocation and return a result value."
  @spec run(Invocation.t(), keyword() | map()) :: Result.t()
  def run(invocation, runtime_options \\ [])

  def run(
        %Invocation{command: %Command{handler: {:projects, action}}} = invocation,
        runtime_options
      ) do
    with_authentication(invocation, runtime_options, &Projects.execute(action, &1, &2))
  end

  def run(
        %Invocation{command: %Command{handler: {:lists, action}}} = invocation,
        runtime_options
      ) do
    with_authentication(invocation, runtime_options, &Lists.execute(action, &1, &2))
  end

  def run(
        %Invocation{command: %Command{handler: {:tasks, action}}} = invocation,
        runtime_options
      ) do
    with_authentication(invocation, runtime_options, &Tasks.execute(action, &1, &2))
  end

  def run(
        %Invocation{command: %Command{handler: {:config, action}}} = invocation,
        runtime_options
      ) do
    ConfigCommand.execute(action, invocation, runtime_options)
  end

  def run(%Invocation{command: %Command{handler: {:completions, :bash}}}, _runtime_options) do
    %Result{stdout: Completions.bash()}
  end

  def run(%Invocation{command: %Command{handler: {:completions, :fish}}}, _runtime_options) do
    %Result{stdout: Completions.fish()}
  end

  def run(
        %Invocation{command: %Command{handler: {:agent, :onboarding}}, globals: globals},
        _runtime_options
      ) do
    text = Onboarding.text()

    if Map.get(globals, :json, false) do
      %Result{stdout: Jason.encode!(%{data: %{onboarding: text}}) <> "\n"}
    else
      %Result{stdout: text}
    end
  end

  def run(
        %Invocation{
          command: %Command{handler: {:agent, :skill_install}},
          options: options,
          globals: globals
        },
        runtime_options
      ) do
    force? = Map.get(options, :force, false) == true
    runtime_options = put_runtime_option(runtime_options, :force, force?)
    json? = Map.get(globals, :json, false)

    case Installer.install(runtime_options) do
      {:ok, result} ->
        render_skill_success(result, json?)

      {:error, :skill_install_failed, message} ->
        envelope = %{"error" => %{"code" => "skill_install_failed", "message" => message}}
        %Result{status: 6, stderr: Output.error(envelope, json?)}
    end
  end

  def run(%Invocation{command: %Command{} = command, globals: globals}, _runtime_options) do
    path = Enum.join(command.path, " ")
    message = "Command handler not installed for taskman #{path}"

    if Map.get(globals, :json, false) do
      %Result{
        status: 5,
        stderr: Jason.encode!(%{error: %{code: "internal_error", message: message}}) <> "\n"
      }
    else
      %Result{status: 5, stderr: "Internal error: #{message}.\n"}
    end
  end

  def run(_invocation, _runtime_options), do: %Result{status: 2, stderr: "Invalid invocation.\n"}

  @doc "Compatibility alias for callers that name dispatch explicitly."
  def execute(invocation, runtime_options \\ []), do: run(invocation, runtime_options)

  defp render_skill_success(result, true),
    do: %Result{stdout: Jason.encode!(%{data: result}) <> "\n"}

  defp render_skill_success(%{action: :installed, path: path}, false),
    do: %Result{stdout: "Installed taskman-cli at #{path}\n"}

  defp render_skill_success(%{action: :updated, path: path}, false),
    do: %Result{stdout: "Updated taskman-cli at #{path}\n"}

  defp render_skill_success(%{action: :current, path: path}, false),
    do: %Result{stdout: "taskman-cli is already current at #{path}\n"}

  defp put_runtime_option(options, key, value) when is_list(options),
    do: Keyword.put(options, key, value)

  defp put_runtime_option(options, key, value) when is_map(options),
    do: Map.put(options, key, value)

  defp put_runtime_option(_options, key, value), do: [{key, value}]

  defp with_authentication(invocation, runtime_options, execute) do
    json? = Map.get(invocation.globals, :json, false)

    case Config.resolve(invocation.globals, runtime_options) do
      {:ok, %{api_url: api_url, api_key: api_key}} when is_binary(api_key) and api_key != "" ->
        invocation = %{invocation | globals: Map.put(invocation.globals, :api_url, api_url)}
        execute.(invocation, resolved_runtime_options(runtime_options, api_key))

      {:ok, _resolved} ->
        authentication_required(json?)

      {:error, :invalid_configuration, message} ->
        invalid_configuration(message, json?)
    end
  end

  defp resolved_runtime_options(options, api_key) when is_list(options) do
    options
    |> Keyword.delete(:api_key)
    |> Keyword.delete(:resolved_api_key)
    |> Keyword.put(:resolved_api_key, api_key)
  end

  defp resolved_runtime_options(options, api_key) when is_map(options) do
    options
    |> Map.delete(:api_key)
    |> Map.delete(:resolved_api_key)
    |> Map.put(:resolved_api_key, api_key)
  end

  defp resolved_runtime_options(_options, api_key), do: [resolved_api_key: api_key]

  defp authentication_required(json?) do
    envelope = %{
      "error" => %{
        "code" => "authentication_required",
        "message" =>
          "A Taskman API key is required. Run taskman config set-key or set TASKMAN_API_KEY."
      }
    }

    %Result{status: 7, stderr: Output.error(envelope, json?)}
  end

  defp invalid_configuration(message, json?) do
    envelope = %{"error" => %{"code" => "invalid_configuration", "message" => message}}
    %Result{status: 2, stderr: Output.error(envelope, json?)}
  end
end
