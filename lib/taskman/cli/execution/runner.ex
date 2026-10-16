defmodule Taskman.CLI.Execution.Runner do
  @moduledoc "Dispatch parsed invocations while keeping IO at the CLI boundary."

  alias Taskman.CLI.{Context, Onboarding}
  alias Taskman.CLI.Commands.Config, as: ConfigCommand
  alias Taskman.CLI.Commands.Lists
  alias Taskman.CLI.Commands.Projects
  alias Taskman.CLI.Commands.Repo
  alias Taskman.CLI.Commands.Tasks
  alias Taskman.CLI.Execution.{Invocation, Result}
  alias Taskman.CLI.Presentation.{Completions, Output}
  alias Taskman.CLI.Registry.Command
  alias Taskman.CLI.Skill.Installer

  @doc "Run a parsed invocation and return a result value."
  @spec run(Invocation.t(), keyword() | map()) :: Result.t()
  def run(invocation, runtime_options \\ [])

  def run(%Invocation{command: %Command{}} = invocation, runtime_options) do
    case prepare_context(invocation, runtime_options) do
      {:ok, invocation, runtime_options} -> dispatch(invocation, runtime_options)
      %Result{} = result -> result
    end
  end

  def run(_invocation, _runtime_options), do: %Result{status: 2, stderr: "Invalid invocation.\n"}

  defp dispatch(
         %Invocation{command: %Command{handler: {:projects, action}}} = invocation,
         runtime_options
       ) do
    Projects.execute(action, invocation, runtime_options)
  end

  defp dispatch(
         %Invocation{command: %Command{handler: {:lists, action}}} = invocation,
         runtime_options
       ) do
    Lists.execute(action, invocation, runtime_options)
  end

  defp dispatch(
         %Invocation{command: %Command{handler: {:tasks, action}}} = invocation,
         runtime_options
       ) do
    Tasks.execute(action, invocation, runtime_options)
  end

  defp dispatch(
         %Invocation{command: %Command{handler: {:config, action}}} = invocation,
         runtime_options
       ) do
    ConfigCommand.execute(action, invocation, snapshot(runtime_options))
  end

  defp dispatch(
         %Invocation{command: %Command{handler: {:repo, action}}} = invocation,
         runtime_options
       ) do
    Repo.execute(action, invocation, snapshot(runtime_options))
  end

  defp dispatch(%Invocation{command: %Command{handler: {:completions, :bash}}}, _runtime_options) do
    %Result{stdout: Completions.bash()}
  end

  defp dispatch(%Invocation{command: %Command{handler: {:completions, :fish}}}, _runtime_options) do
    %Result{stdout: Completions.fish()}
  end

  defp dispatch(
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

  defp dispatch(
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

  defp dispatch(%Invocation{command: %Command{} = command, globals: globals}, _runtime_options) do
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

  defp prepare_context(
         %Invocation{command: %Command{context_role: :local}} = invocation,
         runtime_options
       ),
       do: {:ok, invocation, runtime_options}

  defp prepare_context(invocation, runtime_options) do
    runtime_options = snapshot(runtime_options)

    case Context.resolve(invocation, runtime_options) do
      {:ok, context} ->
        options =
          if context.project_id,
            do: Map.put(invocation.options, :project, context.project_id),
            else: invocation.options

        {:ok, %{invocation | options: options},
         runtime_options
         |> Map.drop([:api_url, :api_key, :resolved_api_key, :resolved_context])
         |> Map.put(:resolved_context, context)}

      {:error, code, message} ->
        envelope = %{"error" => %{"code" => Atom.to_string(code), "message" => message}}

        %Result{
          status: if(code == :authentication_required, do: 7, else: 2),
          stderr: Output.error(envelope, Map.get(invocation.globals, :json, false))
        }
    end
  end

  defp snapshot(options) do
    options = Map.new(options)
    options |> Map.put_new_lazy(:env, &System.get_env/0) |> Map.put_new_lazy(:cwd, &File.cwd!/0)
  end
end
