defmodule Taskman.CLI.Commands.Repo do
  @moduledoc "Manage the clone's shared host and Project binding."
  alias Taskman.CLI.{Client, Config, RepositoryBinding}
  alias Taskman.CLI.Execution.{Invocation, Result}

  @spec execute(atom(), Invocation.t(), keyword() | map()) :: Result.t()
  def execute(action, %Invocation{} = invocation, runtime_options \\ []) do
    options = Map.new(runtime_options)

    result =
      case action do
        :bind ->
          bind(options)

        action ->
          with {:ok, common_dir} <- working_tree(options) do
            case action do
              :unbind ->
                with :ok <- RepositoryBinding.unbind(common_dir, options),
                     do: {:ok, %{unbound: true}, "Removed repository binding."}

              :show ->
                show(common_dir, options)
            end
          end
      end

    Result.from_local(result, Map.get(invocation.globals, :json, false))
  end

  defp bind(options) do
    context = Map.fetch!(options, :resolved_context)

    with {:ok, project} <-
           Client.request(
             :get,
             "/api/v1/projects/#{context.project_id}",
             [],
             options,
             {:member, :project}
           ),
         :ok <- matching_project(project, context.project_id),
         :ok <-
           RepositoryBinding.put(
             context.common_git_dir,
             %{version: 1, host: context.host, project_id: context.project_id},
             options
           ) do
      {:ok, %{bound: true, host: context.host, project_id: context.project_id},
       "Bound repository to #{context.host} Project #{context.project_id}."}
    end
  end

  defp matching_project(%{"id" => id}, id), do: :ok

  defp matching_project(_project, _id),
    do:
      {:error, 5,
       %{
         "error" => %{
           "code" => "invalid_response",
           "message" => "The returned Project ID did not match the requested Project"
         }
       }}

  defp working_tree(options) do
    case RepositoryBinding.discover(options) do
      {:ok, :outside_work_tree} ->
        {:error, :invalid_invocation, "Repository commands require a Git working tree"}

      other ->
        other
    end
  end

  defp show(common_dir, options) do
    with {:ok, binding} <- RepositoryBinding.read(common_dir, options),
         {:ok, registered} <- registered(binding, options) do
      data = %{
        common_git_dir: common_dir,
        binding: if(binding == :unbound, do: nil, else: Map.take(binding, [:host, :project_id])),
        host_registered: registered
      }

      text =
        if binding == :unbound,
          do: "unbound",
          else: "#{binding.host} Project #{binding.project_id}; host registered: #{registered}"

      {:ok, data, "Common Git directory: #{common_dir}\nBinding: #{text}"}
    end
  end

  defp registered(:unbound, _options), do: {:ok, nil}

  defp registered(binding, options) do
    with {:ok, document} <- Config.load(options),
         do: {:ok, Map.has_key?(document.hosts, binding.host)}
  end
end
