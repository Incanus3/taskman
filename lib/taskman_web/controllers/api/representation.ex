defmodule TaskmanWeb.API.Representation do
  @moduledoc """
  Serializers for values returned by the versioned JSON API.
  """

  alias Taskman.Lists
  alias Taskman.Tasks.Comment
  alias Taskman.Tasks.TaskWithLocation

  @spec linked_task(map()) :: map()
  def linked_task(summary) do
    %{
      id: summary.id,
      project_id: summary.project_id,
      project_name: summary.project_name,
      title: summary.title,
      status: summary.status,
      priority: summary.priority,
      location: summary.location
    }
  end

  @spec blocking_edge(map()) :: map()
  def blocking_edge(%{blocking_task: blocker, blocked_task: blocked}) do
    %{blocking_task: linked_task(blocker), blocked_task: linked_task(blocked)}
  end

  @spec project(Taskman.Projects.Project.t()) :: map()
  def project(project) do
    %{
      id: project.id,
      name: project.name,
      description: project.description,
      icon: project.icon,
      color: project.color
    }
  end

  @spec task_list(Taskman.Lists.TaskList.t(), [Taskman.Lists.TaskList.t()]) :: map()
  def task_list(task_list, project_lists) do
    %{
      id: task_list.id,
      project_id: task_list.project_id,
      parent_list_id: task_list.parent_list_id,
      name: task_list.name,
      path: project_lists |> Lists.path_for(task_list) |> Enum.map(& &1.name)
    }
  end

  @spec task(Taskman.Tasks.Task.t(), [Taskman.Lists.TaskList.t()]) :: map()
  def task(task, project_lists) do
    path =
      case task.list_id do
        nil ->
          []

        list_id ->
          owner = Enum.find(project_lists, &(&1.id == list_id))
          project_lists |> Lists.path_for(owner) |> Enum.map(& &1.name)
      end

    task_with_path(task, path)
  end

  @spec task_with_location(TaskWithLocation.t()) :: map()
  def task_with_location(%TaskWithLocation{task: task, location_path: location_path}) do
    task_with_path(task, Enum.map(location_path, & &1.name))
  end

  @spec comment(Comment.t()) :: map()
  def comment(%Comment{} = comment) do
    %{
      id: comment.id,
      task_id: comment.task_id,
      author: %{display_name: comment.author_name, login: comment.author_login},
      text: comment.text,
      created_at: comment.created_at
    }
  end

  defp task_with_path(task, path) do
    %{
      id: task.id,
      project_id: task.project_id,
      list_id: task.list_id,
      parent_task_id: task.parent_task_id,
      title: task.title,
      description: task.description,
      status: task.status,
      priority: task.priority,
      due_at: task.due_at,
      location: %{
        kind: if(is_nil(task.list_id), do: "project", else: "list"),
        list_id: task.list_id,
        path: path
      }
    }
  end

  @spec validation_fields(Ecto.Changeset.t()) :: map()
  def validation_fields(changeset) do
    Ecto.Changeset.traverse_errors(changeset, fn {message, options} ->
      Enum.reduce(options, message, fn {key, value}, rendered ->
        String.replace(rendered, "%{#{key}}", render_error_value(value))
      end)
    end)
  end

  defp render_error_value(value) do
    if String.Chars.impl_for(value) do
      to_string(value)
    else
      inspect(value)
    end
  end
end
