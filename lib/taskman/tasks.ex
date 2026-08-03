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

  def get_task_for_project(%Project{id: project_id}, id) when is_integer(id) and id > 0 do
    Repo.get_by(Task, id: id, project_id: project_id)
  end

  def get_task_for_project(%Project{} = project, id) when is_binary(id) do
    case Integer.parse(id) do
      {parsed, ""} -> get_task_for_project(project, parsed)
      _invalid -> nil
    end
  end

  def get_task_for_project(%Project{}, _id), do: nil

  def create_task(%Project{id: project_id}, attrs \\ %{}) do
    %Task{project_id: project_id}
    |> Task.changeset(attrs)
    |> Repo.insert()
  end

  def change_task(owner, attrs \\ %{})

  def change_task(%Project{id: project_id}, attrs) do
    %Task{project_id: project_id}
    |> Task.changeset(attrs)
  end

  def change_task(%Task{} = task, attrs) do
    Task.changeset(task, attrs)
  end

  def update_task(
        %Project{id: project_id},
        %Task{project_id: project_id} = task,
        attrs
      ) do
    task
    |> Task.changeset(attrs)
    |> Repo.update()
  end

  def update_task(%Project{}, %Task{}, _attrs), do: {:error, :not_found}
end
