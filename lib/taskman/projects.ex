defmodule Taskman.Projects do
  import Ecto.Query

  alias Taskman.ChangeNotifications
  alias Taskman.Projects.Project
  alias Taskman.Repo

  def list_projects do
    Project
    |> order_by([project], asc: project.inserted_at, asc: project.id)
    |> Repo.all()
  end

  def get_project(id) when is_integer(id) and id > 0, do: Repo.get(Project, id)

  def get_project(id) when is_binary(id) do
    case Integer.parse(id) do
      {parsed, ""} -> get_project(parsed)
      _invalid -> nil
    end
  end

  def get_project(_id), do: nil

  def create_project(attrs \\ %{}) do
    changeset = change_project(%Project{}, attrs)

    case Repo.insert(changeset) do
      {:ok, project} = result ->
        _ = ChangeNotifications.publish_project(project, :created, [:name])
        result

      error ->
        error
    end
  end

  def change_project(%Project{} = project, attrs \\ %{}) do
    Project.changeset(project, attrs)
  end
end
