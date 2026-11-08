defmodule Taskman.Tasks.Activity.Persistence do
  @moduledoc false

  import Ecto.Query

  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Repo
  alias Taskman.Tasks.{Activity, Task}
  alias Taskman.Tasks.Activity.{Event, EventTask}

  @type attrs :: %{
          optional(:task_id) => pos_integer(),
          optional(:actor_user_id) => Ecto.UUID.t() | nil,
          optional(:grouping_key) => String.t(),
          optional(:kind) => String.t(),
          optional(:field) => String.t() | nil,
          optional(:before_value) => Event.value_container(),
          optional(:after_value) => Event.value_container(),
          optional(:context) => Event.stored_context(),
          optional(:first_changed_at) => DateTime.t(),
          optional(:last_changed_at) => DateTime.t()
        }
  @type write_result :: {:ok, Event.t()} | {:error, Ecto.Changeset.t()}

  @spec scoped_task(pos_integer(), pos_integer()) :: Task.t() | nil
  def scoped_task(project_id, task_id),
    do: Repo.get_by(Task, project_id: project_id, id: task_id)

  @doc "Locks the latest surviving event; the caller owns the transaction and domain locks."
  @spec lock_latest(String.t()) :: Event.t() | nil
  def lock_latest(grouping_key) do
    Repo.one(
      from event in Event,
        where: event.grouping_key == ^grouping_key,
        order_by: [desc: event.last_changed_at, desc: event.id],
        limit: 1,
        lock: "FOR UPDATE"
    )
  end

  @spec insert(attrs()) :: write_result()
  def insert(attrs), do: Repo.insert(Ecto.Changeset.change(%Event{}, attrs) |> constraints())

  @spec update(Event.t(), attrs()) :: write_result()
  def update(event, attrs),
    do: Repo.update(Ecto.Changeset.change(event, attrs) |> constraints())

  @spec delete(Event.t()) :: write_result()
  def delete(event), do: Repo.delete(event)

  defp constraints(changeset) do
    changeset
    |> Ecto.Changeset.foreign_key_constraint(:task_id)
    |> Ecto.Changeset.foreign_key_constraint(:actor_user_id)
    |> Ecto.Changeset.check_constraint(:kind, name: :task_activity_events_kind_field_check)
    |> Ecto.Changeset.check_constraint(:before_value, name: :task_activity_events_values_check)
    |> Ecto.Changeset.check_constraint(:context, name: :task_activity_events_context_check)
    |> Ecto.Changeset.check_constraint(:last_changed_at, name: :task_activity_events_time_check)
    |> Ecto.Changeset.check_constraint(:grouping_key,
      name: :task_activity_events_grouping_key_check
    )
  end

  @spec event_tasks(pos_integer()) :: [Activity.affected_task()]
  def event_tasks(event_id) do
    Repo.all(
      from event_task in EventTask,
        join: task in Task,
        on: task.id == event_task.task_id,
        where: event_task.event_id == ^event_id,
        select: {task.project_id, task.id}
    )
  end

  @doc "Replaces associations with surviving Task IDs and returns their Project/Task pairs."
  @spec replace_event_tasks(pos_integer(), [pos_integer()]) ::
          [Activity.affected_task()]
  def replace_event_tasks(event_id, task_ids) do
    Repo.delete_all(from event_task in EventTask, where: event_task.event_id == ^event_id)

    # Historical endpoints may have been deleted since entering the event.
    # Resolve surviving associated Tasks and their Projects together.
    pairs =
      Repo.all(
        from task in Task,
          where: task.id in ^task_ids,
          select: {task.project_id, task.id}
      )

    Repo.insert_all(
      EventTask,
      Enum.map(pairs, fn {_, task_id} -> %{event_id: event_id, task_id: task_id} end)
    )

    pairs
  end

  @spec events(pos_integer()) :: [Event.t()]
  def events(task_id) do
    Repo.all(
      from event in Event,
        join: event_task in EventTask,
        on: event_task.event_id == event.id,
        where: event_task.task_id == ^task_id
    )
  end

  @doc """
  Snapshots existing Tasks from IDs or structs, ignoring nil and deleted IDs.
  Supplied Task structs must exist and override their persisted title with the mutation's result.
  """
  @spec task_references([Task.t() | pos_integer() | nil]) ::
          %{optional(pos_integer()) => Event.task_reference()}
  def task_references(tasks) do
    ids =
      tasks
      |> Enum.map(fn
        %Task{id: id} -> id
        id -> id
      end)
      |> Enum.reject(&is_nil/1)
      |> Enum.uniq()

    references =
      Repo.all(
        from task in Task,
          join: project in Project,
          on: task.project_id == project.id,
          where: task.id in ^ids,
          select:
            {task.id,
             %{
               "id" => task.id,
               "project_id" => task.project_id,
               "project_name" => project.name,
               "title" => task.title
             }}
      )
      |> Map.new()

    # The successful mutation's returned Task is authoritative for its own snapshot.
    Enum.reduce(tasks, references, fn
      %Task{} = task, refs ->
        Map.update!(refs, task.id, &%{&1 | "title" => task.title})

      _, refs ->
        refs
    end)
  end

  @spec location(Task.t()) :: Event.location()
  def location(task) do
    lists = Repo.all(from list in TaskList, where: list.project_id == ^task.project_id)
    selected = Enum.find(lists, &(&1.id == task.list_id))

    %{
      "project_id" => task.project_id,
      "project_name" => Repo.get!(Project, task.project_id).name,
      "list_id" => task.list_id,
      "path" => lists |> Taskman.Lists.path_for(selected) |> Enum.map(& &1.name)
    }
  end

  @spec existing_task_ids([pos_integer()]) :: MapSet.t(pos_integer())
  def existing_task_ids(ids) do
    Repo.all(from task in Task, where: task.id in ^ids, select: task.id) |> MapSet.new()
  end
end
