defmodule Taskman.Projects do
  import Ecto.Query

  alias Taskman.ChangeNotifications
  alias Taskman.Projects.Project
  alias Taskman.Repo

  @project_fields [:name, :description, :icon, :color]

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
        _ = ChangeNotifications.publish_project(project, :created, @project_fields)
        result

      error ->
        error
    end
  end

  def update_project(%Project{} = project, attrs) when is_map(attrs) do
    changeset = change_project(project, attrs)
    changed_fields = Enum.filter(@project_fields, &Ecto.Changeset.changed?(changeset, &1))

    if changeset.valid? and changed_fields == [] do
      case Repo.get(Project, project.id) do
        nil -> {:error, Ecto.Changeset.add_error(changeset, :base, "Project no longer exists")}
        current -> {:ok, current}
      end
    else
      case Repo.update(changeset,
             stale_error_field: :base,
             stale_error_message: "Project no longer exists"
           ) do
        {:ok, updated} = result ->
          if changed_fields != [] do
            _ = ChangeNotifications.publish_project(updated, :updated, changed_fields)
          end

          result

        error ->
          error
      end
    end
  end

  def change_project(%Project{} = project, attrs \\ %{}) do
    Project.changeset(project, attrs)
  end
end
