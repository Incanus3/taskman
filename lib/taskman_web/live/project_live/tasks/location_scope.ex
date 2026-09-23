defmodule TaskmanWeb.ProjectLive.Tasks.LocationScope do
  @moduledoc "Pure location visibility policy shared by Task creation and detail recovery."

  alias Taskman.Lists
  alias Taskman.Lists.TaskList

  @spec visible?(TaskList.t() | nil, TaskList.t() | nil, boolean(), [TaskList.t()]) ::
          boolean()
  def visible?(nil, nil, _include_children?, _task_lists), do: true

  def visible?(%TaskList{id: id}, %TaskList{id: id}, _include_children?, _task_lists), do: true

  def visible?(_selected, _actual, false, _task_lists), do: false
  def visible?(%TaskList{}, nil, true, _task_lists), do: false

  def visible?(nil, %TaskList{id: actual_id}, true, task_lists) do
    Enum.any?(task_lists, &(&1.id == actual_id))
  end

  def visible?(%TaskList{id: selected_id}, %TaskList{} = actual, true, task_lists) do
    task_lists
    |> Lists.path_for(actual)
    |> Enum.any?(&(&1.id == selected_id))
  end

  @spec backdrop(map(), TaskList.t() | nil, [TaskList.t()]) :: TaskList.t() | nil
  def backdrop(workspace, actual, task_lists) do
    selected = workspace.selected_list

    if backdrop_exists?(workspace, task_lists) and
         visible?(selected, actual, workspace.include_children?, task_lists) do
      selected
    else
      actual
    end
  end

  defp backdrop_exists?(%{location_not_found?: true}, _task_lists), do: false
  defp backdrop_exists?(%{selected_list: nil}, _task_lists), do: true

  defp backdrop_exists?(%{selected_list: %TaskList{id: selected_id}}, task_lists) do
    Enum.any?(task_lists, &(&1.id == selected_id))
  end
end
