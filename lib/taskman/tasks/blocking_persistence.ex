defmodule Taskman.Tasks.BlockingPersistence do
  @moduledoc false

  import Ecto.Query

  alias Taskman.Repo
  alias Taskman.Lists
  alias Taskman.Lists.TaskList
  alias Taskman.Tasks.BlockingLink
  alias Taskman.Tasks.Task
  alias Taskman.Projects.Project

  @spec outgoing(pos_integer()) :: [BlockingLink.t()]
  def outgoing(task_id) do
    BlockingLink
    |> where([link], link.blocking_task_id == ^task_id)
    |> order_by([link], asc: link.id)
    |> Repo.all()
  end

  @spec incoming(pos_integer()) :: [BlockingLink.t()]
  def incoming(task_id) do
    BlockingLink
    |> where([link], link.blocked_task_id == ^task_id)
    |> order_by([link], asc: link.id)
    |> Repo.all()
  end

  @spec insert(pos_integer() | nil, pos_integer() | nil) ::
          {:ok, BlockingLink.t()} | {:error, Ecto.Changeset.t()}
  def insert(blocking_task_id, blocked_task_id) do
    %BlockingLink{blocking_task_id: blocking_task_id, blocked_task_id: blocked_task_id}
    |> BlockingLink.changeset()
    |> Repo.insert()
  end

  @spec delete(BlockingLink.t()) :: {:ok, BlockingLink.t()} | {:error, Ecto.Changeset.t()}
  def delete(link), do: Repo.delete(link)

  def endpoint(task_id) when is_integer(task_id) and task_id > 0, do: Repo.get(Task, task_id)
  def endpoint(_task_id), do: nil

  def edge(blocking_task_id, blocked_task_id) do
    Repo.get_by(BlockingLink,
      blocking_task_id: blocking_task_id,
      blocked_task_id: blocked_task_id
    )
  end

  def edge?(blocking_task_id, blocked_task_id),
    do: not is_nil(edge(blocking_task_id, blocked_task_id))

  def reachable?(from_task_id, target_task_id) do
    %{rows: [[reachable?]]} =
      Ecto.Adapters.SQL.query!(
        Repo,
        """
        WITH RECURSIVE reachable(id) AS (
          SELECT blocked_task_id FROM task_blocking_links WHERE blocking_task_id = $1
          UNION
          SELECT link.blocked_task_id
          FROM task_blocking_links AS link
          JOIN reachable ON link.blocking_task_id = reachable.id
        )
        SELECT EXISTS(SELECT 1 FROM reachable WHERE id = $2)
        """,
        [from_task_id, target_task_id]
      )

    reachable?
  end

  def summaries(task_ids) do
    task_ids = Enum.uniq(task_ids)

    rows =
      Task
      |> join(:inner, [task], project in Project, on: task.project_id == project.id)
      |> where([task], task.id in ^task_ids)
      |> select([task, project], {task, project.name})
      |> Repo.all()

    project_ids = rows |> Enum.map(fn {task, _} -> task.project_id end) |> Enum.uniq()

    lists =
      TaskList
      |> where([list], list.project_id in ^project_ids)
      |> Repo.all()

    lists_by_id = Map.new(lists, &{&1.id, &1})

    Map.new(rows, fn {task, project_name} ->
      path =
        lists
        |> Lists.path_for(Map.get(lists_by_id, task.list_id))
        |> Enum.map(& &1.name)

      {task.id,
       %{
         id: task.id,
         project_id: task.project_id,
         project_name: project_name,
         title: task.title,
         status: task.status,
         priority: task.priority,
         location: %{
           kind: if(is_nil(task.list_id), do: "project", else: "list"),
           list_id: task.list_id,
           path: path
         }
       }}
    end)
  end
end
