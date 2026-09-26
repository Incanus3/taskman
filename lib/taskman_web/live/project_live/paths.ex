defmodule TaskmanWeb.ProjectLive.Paths do
  use TaskmanWeb, :verified_routes

  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Tasks.Task

  def browse_path(%Project{id: project_id}, nil, _include_children?),
    do: ~p"/projects/#{project_id}"

  def browse_path(%Project{id: project_id}, %TaskList{id: list_id}, _include_children?),
    do: ~p"/projects/#{project_id}/lists/#{list_id}"

  def new_task_path(project, task_list, include_children?, parent_task_id \\ nil)

  def new_task_path(%Project{id: project_id}, nil, _include_children?, parent_task_id) do
    ~p"/projects/#{project_id}/tasks/new"
    |> append_parent_task_id(parent_task_id)
  end

  def new_task_path(
        %Project{id: project_id},
        %TaskList{id: list_id},
        _include_children?,
        parent_task_id
      ) do
    ~p"/projects/#{project_id}/lists/#{list_id}/tasks/new"
    |> append_parent_task_id(parent_task_id)
  end

  def task_detail_path(
        %Project{id: project_id},
        nil,
        %Task{id: task_id},
        _include_children?
      ) do
    ~p"/projects/#{project_id}/tasks/#{task_id}"
  end

  def task_detail_path(
        %Project{id: project_id},
        %TaskList{id: list_id},
        %Task{id: task_id},
        _include_children?
      ) do
    ~p"/projects/#{project_id}/lists/#{list_id}/tasks/#{task_id}"
  end

  def selected_task_route?(
        %{"project_id" => project_id, "task_id" => task_id} = params,
        %Project{id: selected_project_id},
        selected_list,
        %Task{id: selected_task_id},
        _include_children?
      ) do
    project_id == Integer.to_string(selected_project_id) &&
      task_id == Integer.to_string(selected_task_id) &&
      selected_list_route?(params, selected_list)
  end

  def selected_task_route?(_params, _project, _selected_list, _task, _include_children?),
    do: false

  defp selected_list_route?(%{"list_id" => list_id}, %TaskList{id: selected_list_id}) do
    list_id == Integer.to_string(selected_list_id)
  end

  defp selected_list_route?(%{"list_id" => _list_id}, nil), do: false
  defp selected_list_route?(_params, nil), do: true
  defp selected_list_route?(_params, %TaskList{}), do: false

  defp append_parent_task_id(path, nil), do: path

  defp append_parent_task_id(path, parent_task_id) do
    separator = if String.contains?(path, "?"), do: "&", else: "?"
    path <> separator <> "parent_task_id=" <> Integer.to_string(parent_task_id)
  end
end
