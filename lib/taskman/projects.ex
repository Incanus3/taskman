defmodule Taskman.Projects do
  import Ecto.Query

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
    %Project{}
    |> change_project(attrs)
    |> Repo.insert()
  end

  def change_project(%Project{} = project, attrs \\ %{}) do
    attrs = normalize_primary_directory(attrs)

    project
    |> Project.changeset(attrs)
    |> validate_primary_directory()
  end

  defp normalize_primary_directory(attrs) do
    key =
      cond do
        Map.has_key?(attrs, :primary_directory) -> :primary_directory
        Map.has_key?(attrs, "primary_directory") -> "primary_directory"
        true -> nil
      end

    case key && Map.fetch(attrs, key) do
      {:ok, path} when is_binary(path) ->
        normalized =
          case String.trim(path) do
            "" -> ""
            trimmed -> Path.expand(trimmed)
          end

        Map.put(attrs, key, normalized)

      _missing_or_invalid ->
        attrs
    end
  end

  defp validate_primary_directory(changeset) do
    case Ecto.Changeset.get_field(changeset, :primary_directory) do
      path when is_binary(path) and path != "" ->
        if File.dir?(path) do
          changeset
        else
          Ecto.Changeset.add_error(changeset, :primary_directory, "must be an existing directory")
        end

      _blank ->
        changeset
    end
  end
end
