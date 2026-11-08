defmodule TaskmanWeb.ProjectLive.ListEditing do
  @moduledoc """
  Opening, validation, and persistence workflow for List editor state.

  Returns editor changes or persistence outcomes for Workspace to present.
  """

  alias Taskman.Lists
  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias TaskmanWeb.ProjectLive.ListEdit

  @type editor_outcome :: {:changed, ListEdit.t()} | :unchanged
  @type save_outcome ::
          editor_outcome()
          | {:created, TaskList.t(), TaskList.t() | nil}
          | {:renamed, TaskList.t()}

  @spec open(Project.t() | nil, map()) :: editor_outcome()
  def open(selected_project, params) do
    case action_project(selected_project, params) do
      %Project{} = project ->
        case Map.get(params, "kind") do
          "new" -> open_new(project, parent_id_param(params))
          "rename" -> open_rename(project, list_id_param(params))
          _other -> :unchanged
        end

      _project_not_selected ->
        :unchanged
    end
  end

  @spec validate(ListEdit.t(), map()) :: editor_outcome()
  def validate(%ListEdit{} = edit, params) do
    case ListEdit.validate(edit, params) do
      {:ok, validated} -> {:changed, validated}
      {:error, :not_found} -> :unchanged
    end
  end

  @spec save(ListEdit.t(), map()) :: save_outcome()
  def save(%ListEdit{} = edit, params) do
    case {edit, ListEdit.target(edit)} do
      {%ListEdit{project: %Project{} = project}, {:ok, {:new, parent}, _task_list}} ->
        create_list(edit, project, parent, params)

      {%ListEdit{project: %Project{} = project}, {:ok, {:rename, current_list}, _task_list}} ->
        rename_list(edit, project, current_list, params)

      _invalid_form ->
        :unchanged
    end
  end

  defp parent_id_param(params), do: Map.get(params, "parent-id", Map.get(params, "parent_id", ""))

  defp list_id_param(params), do: Map.get(params, "list-id", Map.get(params, "list_id"))

  defp action_project(selected_project, params) do
    project_id = Map.get(params, "project-id", Map.get(params, "project_id"))

    case {selected_project, project_id} do
      {%Project{} = project, nil} ->
        project

      {%Project{id: id} = project, requested_id} when requested_id == id ->
        project

      {%Project{id: id} = project, requested_id} ->
        if to_string(requested_id) == Integer.to_string(id), do: project, else: nil

      _other ->
        nil
    end
  end

  defp open_new(project, parent_id) do
    case resolve_parent_list(project, parent_id) do
      {:ok, parent} -> {:changed, ListEdit.open_new(project, parent)}
      :error -> :unchanged
    end
  end

  defp open_rename(project, list_id) do
    case Lists.get_list_for_project(project, list_id) do
      %TaskList{} = task_list -> {:changed, ListEdit.open_rename(project, task_list)}
      nil -> :unchanged
    end
  end

  defp resolve_parent_list(_project, parent_id) when parent_id in [nil, ""], do: {:ok, nil}

  defp resolve_parent_list(project, parent_id) do
    case Lists.get_list_for_project(project, parent_id) do
      %TaskList{} = parent -> {:ok, parent}
      nil -> :error
    end
  end

  defp create_list(edit, project, parent, params) do
    case Lists.create_list(project, parent, params) do
      {:ok, task_list} -> {:created, task_list, parent}
      {:error, %Ecto.Changeset{} = changeset} -> {:changed, ListEdit.put_error(edit, changeset)}
      {:error, :not_found} -> :unchanged
    end
  end

  defp rename_list(edit, project, current_list, params) do
    case Lists.rename_list(project, current_list, params) do
      {:ok, renamed_list} -> {:renamed, renamed_list}
      {:error, %Ecto.Changeset{} = changeset} -> {:changed, ListEdit.put_error(edit, changeset)}
      {:error, :not_found} -> :unchanged
    end
  end
end
