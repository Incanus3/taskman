defmodule Taskman.Tasks.Comments do
  @moduledoc false

  alias Taskman.Accounts
  alias Taskman.ChangeNotifications
  alias Taskman.Projects.Project
  alias Taskman.Repo
  alias Taskman.Tasks.Comment
  alias Taskman.Tasks.CommentPersistence
  alias Taskman.Tasks.Task

  @spec create(Project.t(), Task.t(), term(), map()) ::
          {:ok, Comment.t()}
          | {:error, Ecto.Changeset.t() | :not_found | :authentication_required}
  def create(%Project{id: project_id}, %Task{id: task_id}, actor, attrs)
      when is_integer(project_id) and is_integer(task_id) and is_map(attrs) do
    if Repo.in_transaction?() do
      raise ArgumentError, "create_comment/4 must be called outside an existing transaction"
    end

    result =
      Repo.transaction(fn ->
        with {:ok, persisted_actor} <- Accounts.lock_eligible_user(actor),
             %Task{} = task <- CommentPersistence.lock_scoped_task(project_id, task_id),
             {:ok, comment} <- CommentPersistence.insert(task, persisted_actor.id, attrs),
             :ok <- CommentPersistence.touch_task(task, comment.created_at) do
          %{comment | author_login: to_string(persisted_actor.email)}
        else
          nil -> Repo.rollback(:not_found)
          {:error, changeset} -> Repo.rollback(translate_insert_error(changeset))
        end
      end)

    case result do
      {:ok, comment} ->
        _ = ChangeNotifications.publish_comment(project_id, task_id, comment.id)
        {:ok, comment}

      {:error, reason} ->
        {:error, reason}
    end
  end

  def create(%Project{}, %Task{}, _actor, _attrs), do: {:error, :not_found}

  defp translate_insert_error(%Ecto.Changeset{errors: errors} = changeset) do
    if Keyword.has_key?(errors, :actor_user_id) do
      :authentication_required
    else
      changeset
    end
  end

  defp translate_insert_error(reason), do: reason
end
