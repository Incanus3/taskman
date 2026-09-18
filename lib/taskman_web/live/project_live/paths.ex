defmodule TaskmanWeb.ProjectLive.Paths do
  use TaskmanWeb, :verified_routes

  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Tasks.Task

  def browse_path(%Project{id: project_id}, nil, include_children?) do
    append_include_children(~p"/projects/#{project_id}", include_children?)
  end

  def browse_path(%Project{id: project_id}, %TaskList{id: list_id}, include_children?) do
    append_include_children(~p"/projects/#{project_id}/lists/#{list_id}", include_children?)
  end

  def new_task_path(project, task_list, include_children?, parent_task_id \\ nil)

  def new_task_path(%Project{id: project_id}, nil, include_children?, parent_task_id) do
    ~p"/projects/#{project_id}/tasks/new"
    |> append_include_children(include_children?)
    |> append_parent_task_id(parent_task_id)
  end

  def new_task_path(
        %Project{id: project_id},
        %TaskList{id: list_id},
        include_children?,
        parent_task_id
      ) do
    ~p"/projects/#{project_id}/lists/#{list_id}/tasks/new"
    |> append_include_children(include_children?)
    |> append_parent_task_id(parent_task_id)
  end

  def task_detail_path(
        %Project{id: project_id},
        nil,
        %Task{id: task_id},
        include_children?
      ) do
    append_include_children(~p"/projects/#{project_id}/tasks/#{task_id}", include_children?)
  end

  def task_detail_path(
        %Project{id: project_id},
        %TaskList{id: list_id},
        %Task{id: task_id},
        include_children?
      ) do
    append_include_children(
      ~p"/projects/#{project_id}/lists/#{list_id}/tasks/#{task_id}",
      include_children?
    )
  end

  def selected_task_route?(
        %{"project_id" => project_id, "task_id" => task_id} = params,
        %Project{id: selected_project_id},
        selected_list,
        %Task{id: selected_task_id},
        include_children?
      ) do
    project_id == Integer.to_string(selected_project_id) &&
      task_id == Integer.to_string(selected_task_id) &&
      selected_list_route?(params, selected_list) &&
      canonical_query_route?(params, include_children?)
  end

  def selected_task_route?(_params, _project, _selected_list, _task, _include_children?),
    do: false

  defp selected_list_route?(%{"list_id" => list_id}, %TaskList{id: selected_list_id}) do
    list_id == Integer.to_string(selected_list_id)
  end

  defp selected_list_route?(%{"list_id" => _list_id}, nil), do: false
  defp selected_list_route?(_params, nil), do: true
  defp selected_list_route?(_params, %TaskList{}), do: false

  defp canonical_query_route?(params, include_children?) do
    Map.get(params, "include_children") == if(include_children?, do: "true", else: nil)
  end

  defp append_include_children(path, true), do: path <> "?include_children=true"
  defp append_include_children(path, false), do: path

  defp append_parent_task_id(path, nil), do: path

  defp append_parent_task_id(path, parent_task_id) do
    separator = if String.contains?(path, "?"), do: "&", else: "?"
    path <> separator <> "parent_task_id=" <> Integer.to_string(parent_task_id)
  end
end
