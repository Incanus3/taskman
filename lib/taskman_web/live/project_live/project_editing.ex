defmodule TaskmanWeb.ProjectLive.ProjectEditing do
  @moduledoc """
  Validation and persistence workflow for Project editor state.

  Returns editor changes or persistence outcomes for the caller to present.
  """

  alias Taskman.Projects
  alias Taskman.Projects.Project
  alias TaskmanWeb.ProjectLive.ProjectEdit

  @type editor_outcome :: {:changed, ProjectEdit.t()} | {:unchanged, ProjectEdit.t()}
  @type save_outcome :: editor_outcome() | {:created, Project.t()} | {:updated, Project.t()}

  @spec validate(ProjectEdit.t(), map()) :: editor_outcome()
  def validate(%ProjectEdit{} = edit, params) do
    case ProjectEdit.validate(edit, params) do
      {:ok, validated} -> {:changed, validated}
      {:error, :not_found} -> {:unchanged, edit}
    end
  end

  @spec save(ProjectEdit.t(), map()) :: save_outcome()
  def save(%ProjectEdit{} = edit, params) do
    case ProjectEdit.target(edit) do
      {:ok, :new, _project} ->
        validate_submission(edit, params)

      {:ok, :edit, project} ->
        case Projects.get_project(project.id) do
          nil -> {:changed, ProjectEdit.reconcile(edit, [])}
          current -> validate_submission(ProjectEdit.reconcile(edit, [current]), params)
        end

      :error ->
        {:unchanged, edit}
    end
  end

  defp validate_submission(edit, params) do
    case validate(edit, params) do
      {:changed, validated} when validated.changeset.valid? -> persist(validated)
      outcome -> outcome
    end
  end

  defp persist(edit) do
    attrs = Map.take(edit.changeset.params || %{}, ["name", "description", "icon", "color"])
    attrs = Map.put(attrs, "color", Ecto.Changeset.get_field(edit.changeset, :color))

    case ProjectEdit.target(edit) do
      {:ok, :new, _project} ->
        persistence_outcome(Projects.create_project(attrs), :created, edit)

      {:ok, :edit, project} ->
        case Projects.get_project(project.id) do
          nil ->
            {:changed, ProjectEdit.reconcile(edit, Projects.list_projects())}

          current ->
            persistence_outcome(Projects.update_project(current, attrs), :updated, edit)
        end

      :error ->
        {:unchanged, edit}
    end
  end

  defp persistence_outcome({:ok, project}, operation, _edit), do: {operation, project}

  defp persistence_outcome({:error, changeset}, _operation, edit),
    do: {:changed, ProjectEdit.put_error(edit, changeset)}
end
