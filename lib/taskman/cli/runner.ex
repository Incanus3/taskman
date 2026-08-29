defmodule Taskman.CLI.Runner do
  @moduledoc "Dispatch parsed invocations while keeping IO at the CLI boundary."

  alias Taskman.CLI.{Command, Completions, Invocation, Onboarding, Output, Result}
  alias Taskman.CLI.Commands.Lists
  alias Taskman.CLI.Commands.Projects
  alias Taskman.CLI.Commands.Tasks
  alias Taskman.CLI.Skill.Installer

  @doc "Run a parsed invocation and return a result value."
  @spec run(Invocation.t(), keyword() | map()) :: Result.t()
  def run(invocation, runtime_options \\ [])

  def run(
        %Invocation{command: %Command{handler: {:projects, action}}} = invocation,
        runtime_options
      ) do
    Projects.execute(action, invocation, runtime_options)
  end

  def run(
        %Invocation{command: %Command{handler: {:lists, action}}} = invocation,
        runtime_options
      ) do
    Lists.execute(action, invocation, runtime_options)
  end

  def run(
        %Invocation{command: %Command{handler: {:tasks, action}}} = invocation,
        runtime_options
      ) do
    Tasks.execute(action, invocation, runtime_options)
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
end
