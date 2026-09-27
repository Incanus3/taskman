defmodule TaskmanWeb.ProjectLive.Tasks.CommentDeparture do
  @moduledoc false

  alias Taskman.Lists
  alias Taskman.Projects
  alias Taskman.Projects.Project

  defstruct destination: nil,
            origin: nil,
            confirming?: false,
            submitting?: false,
            error: nil

  def empty, do: %__MODULE__{}

  def request(
        %__MODULE__{destination: destination, confirming?: true} = state,
        _task,
        _path,
        _draft,
        _origin
      )
      when not is_nil(destination),
      do: {:pending, state}

  def request(
        %__MODULE__{destination: destination, confirming?: false} = state,
        task,
        path,
        draft,
        _origin
      )
      when not is_nil(destination) and is_binary(path) and is_binary(draft) do
    cond do
      not local_path?(path) -> {:pending, state}
      same_task?(path, state.origin, task) -> {:continue, %{state | origin: path}}
      String.trim(draft) == "" -> {:continue, empty()}
      true -> {:confirm, %{state | confirming?: true, error: nil}}
    end
  end

  def request(%__MODULE__{} = state, task, path, draft, origin)
      when is_binary(path) and is_binary(draft) do
    cond do
      not local_path?(path) ->
        {:pending, state}

      same_task?(path, origin, task) ->
        {:continue, state}

      String.trim(draft) == "" ->
        {:continue, state}

      true ->
        {:confirm, %{state | destination: path, origin: origin, confirming?: true}}
    end
  end

  def request(state, _task, _path, _draft, _origin), do: {:pending, state}

  def cancel(_state), do: empty()

  def post_failed(%__MODULE__{} = state) do
    %{state | confirming?: false, submitting?: false, error: nil}
  end

  def clear_retained(%__MODULE__{destination: destination, confirming?: false})
      when not is_nil(destination),
      do: empty()

  def clear_retained(%__MODULE__{} = state), do: state

  def local_path?("/" <> rest), do: not String.starts_with?(rest, "/")
  def local_path?(_), do: false

  def same_task?(path, origin, {%Project{} = project, task_id}) when is_integer(task_id) do
    with {project_id, list_id, task_id_string} <- task_route(path),
         {^project_id, _origin_list_id, ^task_id_string} <- task_route(origin),
         true <- project_id == Integer.to_string(project.id),
         true <- task_id_string == Integer.to_string(task_id),
         %Project{} = current_project <- Projects.get_project(project.id),
         true <- valid_location?(current_project, list_id) do
      true
    else
      _ -> false
    end
  end

  def same_task?(_path, _origin, _task_id), do: false

  defp task_route(path) when is_binary(path) do
    case Regex.run(
           ~r{\A/projects/([1-9][0-9]*)(?:/lists/([1-9][0-9]*))?/tasks/([1-9][0-9]*)\z},
           URI.parse(path).path || ""
         ) do
      [_, project_id, "", task_id] -> {project_id, nil, task_id}
      [_, project_id, list_id, task_id] -> {project_id, list_id, task_id}
      _ -> nil
    end
  end

  defp task_route(_path), do: nil

  defp valid_location?(_project, nil), do: true

  defp valid_location?(project, list_id),
    do: not is_nil(Lists.get_list_for_project(project, list_id))
end
