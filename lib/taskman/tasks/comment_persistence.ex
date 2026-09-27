defmodule Taskman.Tasks.CommentPersistence do
  @moduledoc false

  import Ecto.Query

  alias Taskman.Repo
  alias Taskman.Tasks.Comment
  alias Taskman.Tasks.Task

  @spec list_for_task(pos_integer()) :: [Comment.t()]
  def list_for_task(task_id) do
    Comment
    |> where([comment], comment.task_id == ^task_id)
    |> order_by([comment], asc: comment.created_at, asc: comment.id)
    |> Repo.all()
  end

  @spec lock_scoped_task(pos_integer(), pos_integer()) :: Task.t() | nil
  def lock_scoped_task(project_id, task_id) do
    Repo.one(
      from task in Task,
        where: task.project_id == ^project_id and task.id == ^task_id,
        lock: "FOR UPDATE"
    )
  end

  @spec insert(Task.t(), Ecto.UUID.t(), map()) ::
          {:ok, Comment.t()} | {:error, Ecto.Changeset.t()}
  def insert(task, actor_id, attrs) do
    %Comment{task_id: task.id, actor_user_id: actor_id}
    |> Comment.changeset(attrs)
    |> Repo.insert()
  end

  @spec touch_task(Task.t(), DateTime.t()) :: :ok
  def touch_task(task, created_at) do
    {1, _} =
      from(current in Task,
        where: current.id == ^task.id,
        update: [set: [updated_at: ^created_at]]
      )
      |> Repo.update_all([])

    :ok
  end
end
