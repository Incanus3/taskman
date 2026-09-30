defmodule Taskman.Tasks.Blocking do
  @moduledoc "Directed Task blocking relationships and their graph invariants."

  alias Taskman.Projects.Project
  alias Taskman.Repo
  alias Taskman.Tasks.BlockingLink
  alias Taskman.Tasks.BlockingPersistence
  alias Taskman.Tasks.GraphLock
  alias Taskman.Tasks.Task

  @spec list(Project.t(), Task.t()) :: {:ok, map()} | {:error, :not_found}
  def list(%Project{} = project, %Task{} = selected_task) do
    with %Task{} <- scoped_endpoint(project, selected_task) do
      outgoing = BlockingPersistence.outgoing(selected_task.id)
      incoming = BlockingPersistence.incoming(selected_task.id)

      ids =
        Enum.map(outgoing, & &1.blocked_task_id) ++
          Enum.map(incoming, & &1.blocking_task_id)

      summaries = BlockingPersistence.summaries(ids)

      {:ok,
       %{
         blocks: ordered_summaries(outgoing, :blocked_task_id, summaries),
         blocked_by: ordered_summaries(incoming, :blocking_task_id, summaries)
       }}
    else
      nil -> {:error, :not_found}
    end
  end

  @spec add(Project.t(), Task.t(), Task.t()) :: {:ok, map()} | {:error, term()}
  def add(%Project{} = blocker_project, %Task{} = blocker, %Task{} = blocked) do
    transact(fn ->
      with {:ok, source, target} <- reload_endpoints(blocker_project, blocker, blocked),
           :ok <- validate_add(source, target),
           {:ok, _link} <- BlockingPersistence.insert(source.id, target.id) do
        edge_summary(source, target)
      else
        {:error, reason} -> Repo.rollback(reason)
      end
    end)
  end

  @spec remove(Project.t(), Task.t(), Task.t()) :: {:ok, map()} | {:error, term()}
  def remove(%Project{} = blocker_project, %Task{} = blocker, %Task{} = blocked) do
    transact(fn ->
      with {:ok, source, target} <- reload_endpoints(blocker_project, blocker, blocked),
           %BlockingLink{} = link <- BlockingPersistence.edge(source.id, target.id),
           {:ok, _deleted} <- BlockingPersistence.delete(link) do
        edge_summary(source, target)
      else
        nil -> Repo.rollback(:not_found)
        {:error, reason} -> Repo.rollback(reason)
      end
    end)
  end

  defp transact(fun) do
    case Repo.transaction(fn ->
           GraphLock.acquire!()
           fun.()
         end) do
      {:ok, result} -> {:ok, result}
      {:error, reason} -> {:error, reason}
    end
  end

  defp reload_endpoints(project, blocker, blocked) do
    case {scoped_endpoint(project, blocker), BlockingPersistence.endpoint(blocked.id)} do
      {%Task{} = source, %Task{} = target} -> {:ok, source, target}
      _ -> {:error, :not_found}
    end
  end

  defp scoped_endpoint(%Project{id: project_id}, %Task{id: task_id}) do
    case BlockingPersistence.endpoint(task_id) do
      %Task{project_id: ^project_id} = task -> task
      _ -> nil
    end
  end

  defp validate_add(%Task{id: same_id}, %Task{id: same_id}),
    do: validation_error("cannot block itself")

  defp validate_add(source, target) do
    cond do
      BlockingPersistence.edge?(source.id, target.id) ->
        validation_error("already blocks this Task")

      target.parent_task_id == source.id ->
        validation_error("a parent cannot block its child")

      BlockingPersistence.reachable?(target.id, source.id) ->
        validation_error("would create a cycle")

      true ->
        :ok
    end
  end

  defp validation_error(message) do
    changeset =
      %BlockingLink{}
      |> Ecto.Changeset.change()
      |> Ecto.Changeset.add_error(:target_task_id, message)

    {:error, changeset}
  end

  defp edge_summary(source, target) do
    summaries = BlockingPersistence.summaries([source.id, target.id])

    %{
      blocking_task: Map.fetch!(summaries, source.id),
      blocked_task: Map.fetch!(summaries, target.id)
    }
  end

  defp ordered_summaries(links, field, summaries) do
    links
    |> Enum.flat_map(fn link ->
      case Map.fetch(summaries, Map.fetch!(link, field)) do
        {:ok, summary} -> [summary]
        :error -> []
      end
    end)
    |> Enum.sort_by(fn summary ->
      {String.downcase(summary.project_name), String.downcase(summary.title), summary.id}
    end)
  end
end
