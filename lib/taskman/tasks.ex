defmodule Taskman.Tasks do
  import Ecto.Query

  alias Taskman.Projects.Project
  alias Taskman.Repo
  alias Taskman.Tasks.Task

  def list_tasks_for_project(%Project{id: project_id}) do
    Task
    |> where([task], task.project_id == ^project_id)
    |> order_by([task], asc: task.inserted_at, asc: task.id)
    |> Repo.all()
  end

  def create_task(%Project{id: project_id}, attrs \\ %{}) do
    %Task{project_id: project_id}
    |> Task.changeset(attrs)
    |> Repo.insert()
  end

  def change_task(%Task{} = task, attrs \\ %{}) do
    Task.changeset(task, attrs)
  end
end
