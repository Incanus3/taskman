defmodule Taskman.ChangeNotifications do
  alias Phoenix.PubSub
  alias Taskman.ChangeNotifications.Event
  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Tasks.Task

  @workspace_topic "workspace:changes"
  @field_order [
    :name,
    :primary_directory,
    :description,
    :due_at,
    :list_id,
    :parent_task_id,
    :priority,
    :project_id,
    :status,
    :title,
    :parent_list_id
  ]

  @type publication_result :: :ok | {:error, term()}

  @spec subscribe_workspace() :: publication_result()
  def subscribe_workspace do
    pubsub_call(fn -> PubSub.subscribe(pubsub_server(), @workspace_topic) end)
  end

  @spec subscribe_project(Project.t() | pos_integer()) :: publication_result()
  def subscribe_project(%Project{id: project_id}), do: subscribe_project(project_id)

  def subscribe_project(project_id) when is_integer(project_id) and project_id > 0 do
    pubsub_call(fn -> PubSub.subscribe(pubsub_server(), project_topic(project_id)) end)
  end

  @spec unsubscribe_project(Project.t() | pos_integer()) :: :ok
  def unsubscribe_project(%Project{id: project_id}), do: unsubscribe_project(project_id)

  def unsubscribe_project(project_id) when is_integer(project_id) and project_id > 0 do
    case pubsub_call(fn -> PubSub.unsubscribe(pubsub_server(), project_topic(project_id)) end) do
      :ok -> :ok
      {:error, _reason} -> :ok
    end
  end

  @spec publish_project(Project.t(), :created, [atom()]) :: publication_result()
  def publish_project(
        %Project{id: project_id},
        :created,
        fields
      )
      when is_integer(project_id) and project_id > 0 and is_list(fields) do
    event = %Event{
      entity: :project,
      operation: :created,
      project_id: project_id,
      entity_id: project_id,
      lock_version: nil,
      fields: normalize_fields(fields)
    }

    publish(@workspace_topic, event)
  end

  @spec publish_list(TaskList.t(), :created | :updated, [atom()]) :: publication_result()
  def publish_list(
        %TaskList{id: entity_id, project_id: project_id},
        operation,
        fields
      )
      when operation in [:created, :updated] and is_integer(project_id) and project_id > 0 and
             is_integer(entity_id) and entity_id > 0 and is_list(fields) do
    event = %Event{
      entity: :list,
      operation: operation,
      project_id: project_id,
      entity_id: entity_id,
      lock_version: nil,
      fields: normalize_fields(fields)
    }

    publish(@workspace_topic, event)
  end

  @spec publish_task(Task.t(), :created | :updated | :moved, [atom()]) :: publication_result()
  def publish_task(
        %Task{id: entity_id, project_id: project_id} = task,
        operation,
        fields
      )
      when operation in [:created, :updated, :moved] and is_integer(project_id) and project_id > 0 and
             is_integer(entity_id) and entity_id > 0 and is_list(fields) do
    event = %Event{
      entity: :task,
      operation: operation,
      project_id: project_id,
      entity_id: entity_id,
      lock_version: Map.get(task, String.to_existing_atom("lock_version")),
      fields: normalize_fields(fields)
    }

    publish(project_topic(project_id), event)
  end

  defp publish(topic, event) do
    pubsub_call(fn -> PubSub.broadcast_from(pubsub_server(), self(), topic, event) end)
  end

  defp pubsub_server do
    Application.get_env(:taskman, :change_notifications_pubsub, Taskman.PubSub)
  end

  defp project_topic(project_id), do: "projects:#{project_id}:tasks"

  defp normalize_fields(fields) do
    unless Enum.all?(fields, &is_atom/1) do
      raise ArgumentError, "fields must contain only atoms"
    end

    fields
    |> Enum.uniq()
    |> Enum.sort_by(&field_sort_key/1)
  end

  defp field_sort_key(field) when is_atom(field) do
    case Enum.find_index(@field_order, &(&1 == field)) do
      nil -> {1, Atom.to_string(field)}
      index -> {0, index}
    end
  end

  defp pubsub_call(fun) do
    try do
      fun.()
    rescue
      exception -> {:error, exception}
    catch
      kind, reason -> {:error, {kind, reason}}
    end
  end
end
