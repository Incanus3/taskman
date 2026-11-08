defmodule Taskman.Tasks.Activity do
  @moduledoc false

  alias Taskman.{Accounts, Repo}
  alias Taskman.Accounts.User
  alias Taskman.Projects.Project
  alias Taskman.Tasks.Activity.{Consolidation, Event, Persistence}
  alias Taskman.Tasks.{BlockingPersistence, Comment, CommentPersistence, Mutations, Task}

  @type affected_task :: {project_id :: pos_integer(), task_id :: pos_integer()}
  @type record_result :: {:ok, [affected_task()]} | {:error, Ecto.Changeset.t()}
  @type list_options :: [include_comments: boolean()]
  @type task_reference :: %{
          id: pos_integer(),
          project_id: pos_integer(),
          project_name: String.t(),
          title: String.t(),
          available?: boolean()
        }
  @type location :: %{
          list_id: pos_integer() | nil,
          project_id: pos_integer(),
          project_name: String.t(),
          path: [String.t()]
        }
  @type value :: nil | boolean() | String.t() | task_reference() | location()
  @typedoc """
  A `Taskman.Tasks.Activity.Event.stored_context/0` projected to atom keys and
  Task references enriched with current `available?` metadata.

  Every event has its owning `:task` snapshot. Blocking changes additionally carry
  both `:blocking_task` and `:blocked_task`; the owning Task is the blocker.
  """
  @type projected_context ::
          %{task: task_reference()}
          | %{
              task: task_reference(),
              blocking_task: task_reference(),
              blocked_task: task_reference()
            }
  @type change :: %{
          type: String.t(),
          task_id: pos_integer(),
          field: String.t() | nil,
          actor: %{user_id: Ecto.UUID.t() | nil, login: String.t()},
          before: value(),
          after: value(),
          context: projected_context(),
          first_changed_at: DateTime.t(),
          last_changed_at: DateTime.t()
        }
  @type entry ::
          %{
            id: String.t(),
            kind: :change,
            occurred_at: DateTime.t(),
            change: change()
          }
          | %{
              id: String.t(),
              kind: :comment,
              occurred_at: DateTime.t(),
              comment: Comment.t()
            }
  @type list_result :: {:ok, [entry()]} | {:error, :not_found}
  @type edge_summary :: %{
          blocking_task: BlockingPersistence.summary(),
          blocked_task: BlockingPersistence.summary()
        }

  @fields [:title, :status, :priority, :due_at, :list_id, :parent_task_id]

  @doc """
  Returns chronological changes and, with `:include_comments`, comments for a scoped Task.
  Historical Task references retain labels and include current `available?` metadata.
  """
  @spec list(Project.t(), Task.t()) :: list_result()
  @spec list(Project.t(), Task.t(), list_options()) :: list_result()
  def list(%Project{id: project_id}, %Task{id: task_id}, opts \\ []) do
    if is_integer(task_id) and Persistence.scoped_task(project_id, task_id) do
      events = Persistence.events(task_id)

      comments =
        if Keyword.get(opts, :include_comments, false),
          do: CommentPersistence.list_for_task(task_id),
          else: []

      logins =
        (Enum.map(events, & &1.actor_user_id) ++ Enum.map(comments, & &1.actor_user_id))
        |> Enum.reject(&is_nil/1)
        |> Enum.uniq()
        |> Accounts.emails_by_user_id()

      existing_ids =
        events
        |> Enum.flat_map(fn event ->
          reference_ids([event.before_value["value"], event.after_value["value"], event.context])
        end)
        |> Enum.uniq()
        |> Persistence.existing_task_ids()

      entries =
        Enum.map(events, &event_entry(&1, logins, existing_ids)) ++
          Enum.map(comments, fn comment ->
            %{
              id: "comment-#{comment.id}",
              kind: :comment,
              occurred_at: comment.created_at,
              comment: %{
                comment
                | author_login: Map.get(logins, comment.actor_user_id, "Deleted user")
              }
            }
          end)

      {:ok, Enum.sort_by(entries, &sort_key/1)}
    else
      {:error, :not_found}
    end
  end

  @doc """
  Records creation (nil previous Task) or tracked field changes and returns affected ID pairs.
  Requires a caller-owned transaction and actor/domain locks; callers must roll back on error.
  """
  @spec record_task(User.t(), Task.t() | nil, Task.t(), Mutations.changed_fields()) ::
          record_result()
  # Sample the clock only after those locks have been acquired.
  def record_task(actor, before_task, persisted_task, changed_fields) do
    require_transaction!()

    fields =
      if before_task do
        changed_fields
        |> Enum.filter(&(&1 in @fields))
        |> Enum.uniq()
        |> Enum.reject(&(Map.fetch!(before_task, &1) == Map.fetch!(persisted_task, &1)))
      else
        []
      end

    if is_nil(before_task) or fields != [] do
      record_task_changes(actor, before_task, persisted_task, fields)
    else
      {:ok, []}
    end
  end

  defp record_task_changes(actor, before_task, persisted_task, fields) do
    now = DateTime.utc_now()

    parents =
      if :parent_task_id in fields,
        do: [before_task.parent_task_id, persisted_task.parent_task_id],
        else: []

    references = Persistence.task_references([persisted_task | parents])

    context = %{"task" => Map.fetch!(references, persisted_task.id)}

    if is_nil(before_task) do
      attrs =
        base_attrs(
          actor,
          persisted_task.id,
          "task:#{persisted_task.id}:created",
          "task_created",
          nil,
          {nil, nil},
          context,
          now
        )

      persist(nil, attrs, [persisted_task.id], :insert)
    else
      Enum.reduce_while(
        fields,
        {:ok, []},
        &record_field_change(&1, &2, actor, before_task, persisted_task, references, context, now)
      )
    end
  end

  @doc "Records a blocking transition under caller-owned transaction and actor/domain locks."
  @spec record_block(User.t(), edge_summary(), boolean(), boolean()) :: record_result()
  def record_block(actor, edge_summary, before_value, after_value) do
    require_transaction!()

    if before_value == after_value do
      {:ok, []}
    else
      source = reference_from_summary(edge_summary.blocking_task)
      target = reference_from_summary(edge_summary.blocked_task)
      context = %{"task" => source, "blocking_task" => source, "blocked_task" => target}

      base_attrs(
        actor,
        source["id"],
        "block:#{source["id"]}:#{target["id"]}",
        "block_changed",
        nil,
        {before_value, after_value},
        context,
        DateTime.utc_now()
      )
      |> transition()
    end
  end

  defp transition(attrs) do
    candidate = Persistence.lock_latest(attrs.grouping_key)

    action =
      Consolidation.action(
        candidate,
        attrs.actor_user_id,
        attrs.before_value["value"],
        attrs.after_value["value"],
        attrs.last_changed_at
      )

    # A consolidated event keeps the original endpoint and its historical labels.
    effective =
      if action in [:update, :delete],
        do: %{attrs | before_value: candidate.before_value},
        else: attrs

    persist(candidate, effective, event_task_ids(effective), action)
  end

  defp persist(candidate, _attrs, _task_ids, :delete) do
    former = Persistence.event_tasks(candidate.id)
    with {:ok, _} <- Persistence.delete(candidate), do: {:ok, former}
  end

  defp persist(candidate, attrs, task_ids, action) do
    former =
      if candidate && action == :update, do: Persistence.event_tasks(candidate.id), else: []

    result =
      case action do
        :insert ->
          Persistence.insert(attrs)

        :update ->
          Persistence.update(
            candidate,
            Map.take(attrs, [:after_value, :context, :last_changed_at])
          )
      end

    with {:ok, event} <- result do
      current = Persistence.replace_event_tasks(event.id, task_ids)
      {:ok, Enum.uniq(former ++ current)}
    end
  end

  defp event_task_ids(%{kind: "block_changed", context: context}),
    do: [context["blocking_task"]["id"], context["blocked_task"]["id"]]

  defp event_task_ids(%{field: "parent_task_id"} = attrs),
    do:
      Enum.reject(
        [
          attrs.task_id,
          reference_id(attrs.before_value["value"]),
          reference_id(attrs.after_value["value"])
        ],
        &is_nil/1
      )

  defp event_task_ids(attrs), do: [attrs.task_id]

  defp base_attrs(actor, task_id, key, kind, field, {before_value, after_value}, context, now) do
    %{
      task_id: task_id,
      actor_user_id: actor.id,
      grouping_key: key,
      kind: kind,
      field: field,
      before_value: %{"value" => before_value},
      after_value: %{"value" => after_value},
      context: context,
      first_changed_at: now,
      last_changed_at: now
    }
  end

  defp field_value(task, :parent_task_id, references),
    do: Map.get(references, task.parent_task_id)

  defp field_value(task, field, _references), do: field_value(task, field)

  defp field_value(task, :list_id), do: Persistence.location(task)
  defp field_value(task, :due_at), do: task.due_at && NaiveDateTime.to_iso8601(task.due_at)

  defp field_value(task, field) when field in [:status, :priority],
    do: Atom.to_string(Map.fetch!(task, field))

  defp field_value(task, field), do: Map.fetch!(task, field)

  defp reference_from_summary(summary) do
    Map.new(
      [:id, :project_id, :project_name, :title],
      &{Atom.to_string(&1), Map.fetch!(summary, &1)}
    )
  end

  defp require_transaction! do
    unless Repo.in_transaction?(),
      do:
        raise(
          ArgumentError,
          "activity recording requires a caller-owned transaction and domain locks"
        )
  end

  defp event_entry(event, logins, existing_ids) do
    %{
      id: "event-#{event.id}",
      kind: :change,
      occurred_at: event.last_changed_at,
      change: %{
        type: event.kind,
        task_id: event.task_id,
        field: event.field,
        actor: %{
          user_id: event.actor_user_id,
          login: Map.get(logins, event.actor_user_id, "Deleted user")
        },
        before: project_value(event.before_value["value"], existing_ids),
        after: project_value(event.after_value["value"], existing_ids),
        context: project_context(event.context, existing_ids),
        first_changed_at: event.first_changed_at,
        last_changed_at: event.last_changed_at
      }
    }
  end

  @spec project_context(Event.stored_context(), MapSet.t(pos_integer())) ::
          projected_context()
  defp project_context(context, existing_ids) do
    [:task, :blocking_task, :blocked_task]
    |> Enum.filter(&Map.has_key?(context, Atom.to_string(&1)))
    |> Map.new(&{&1, project_value(context[Atom.to_string(&1)], existing_ids)})
  end

  defp project_value(%{"id" => id} = ref, existing_ids) do
    %{
      id: id,
      project_id: ref["project_id"],
      project_name: ref["project_name"],
      title: ref["title"],
      available?: MapSet.member?(existing_ids, id)
    }
  end

  defp project_value(%{"list_id" => id} = location, _existing_ids),
    do: %{
      list_id: id,
      project_id: location["project_id"],
      project_name: location["project_name"],
      path: location["path"]
    }

  defp project_value(value, _existing_ids), do: value

  defp reference_id(%{"id" => id}), do: id
  defp reference_id(_), do: nil
  defp reference_ids(values) when is_list(values), do: Enum.flat_map(values, &reference_ids/1)
  defp reference_ids(%{"id" => id}), do: [id]
  defp reference_ids(values) when is_map(values), do: values |> Map.values() |> reference_ids()
  defp reference_ids(_), do: []

  defp sort_key(entry) do
    {DateTime.to_unix(entry.occurred_at, :microsecond),
     if(entry.kind == :comment, do: 0, else: 1), entry_numeric_id(entry)}
  end

  defp entry_numeric_id(%{id: "comment-" <> id}), do: String.to_integer(id)
  defp entry_numeric_id(%{id: "event-" <> id}), do: String.to_integer(id)

  defp record_field_change(
         field,
         {:ok, affected},
         actor,
         before_task,
         persisted_task,
         references,
         context,
         now
       ) do
    old = field_value(before_task, field, references)
    new = field_value(persisted_task, field, references)

    attrs =
      base_attrs(
        actor,
        persisted_task.id,
        "task:#{persisted_task.id}:#{field}",
        "field_changed",
        Atom.to_string(field),
        {old, new},
        context,
        now
      )

    case transition(attrs) do
      {:ok, affected_tasks} -> {:cont, {:ok, Enum.uniq(affected ++ affected_tasks)}}
      {:error, reason} -> {:halt, {:error, reason}}
    end
  end
end
