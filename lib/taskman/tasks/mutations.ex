defmodule Taskman.Tasks.Mutations do
  import Ecto.Query

  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Repo
  alias Taskman.Tasks.Conflict
  alias Taskman.Tasks.Hierarchy
  alias Taskman.Tasks.Task

  @creation_fields [
    :description,
    :due_at,
    :list_id,
    :parent_task_id,
    :priority,
    :project_id,
    :status,
    :title
  ]
  @editable_fields [:description, :due_at, :priority, :status, :title]
  @field_order [
    :description,
    :due_at,
    :list_id,
    :parent_task_id,
    :priority,
    :project_id,
    :status,
    :title
  ]

  def create(project, task, attrs, opts) do
    if Keyword.has_key?(opts, :parent) do
      create_with_parent(project, task, attrs, Keyword.get(opts, :parent))
    else
      case Repo.insert(Task.changeset(task, attrs)) do
        {:ok, persisted} -> {:ok, persisted, @creation_fields}
        error -> error
      end
    end
  end

  def update(project, task, attrs, opts) do
    changeset = Task.changeset(task, attrs)

    if changeset.valid? do
      intended = Map.take(changeset.changes, @editable_fields)

      if Keyword.has_key?(opts, :parent) do
        update_with_parent(project, task, intended, Keyword.get(opts, :parent))
      else
        update_ordinary(project, task, intended)
      end
    else
      Repo.update(changeset)
    end
  end

  def move(project, task, destination_id) do
    case destination(project, destination_id) do
      :ok -> move_with_destination(project, task, destination_id)
      error -> error
    end
  end

  defp create_with_parent(project, task, attrs, parent) do
    case Repo.transaction(fn ->
           with %Project{} = locked_project <- lock_project(project),
                {:ok, persisted_parent} <- reload_parent(locked_project, parent),
                :ok <- Hierarchy.validate_parent(task, persisted_parent, locked_project) do
             changeset =
               task
               |> Task.changeset(attrs)
               |> Ecto.Changeset.put_change(:parent_task_id, parent_id(persisted_parent))

             case Repo.insert(changeset) do
               {:ok, persisted} -> persisted
               {:error, error_changeset} -> Repo.rollback(error_changeset)
             end
           else
             nil -> Repo.rollback(:not_found)
             {:error, :not_found} -> Repo.rollback(:not_found)
             {:error, :cycle} -> Repo.rollback(cycle_changeset(task, attrs))
           end
         end) do
      {:ok, persisted} -> {:ok, persisted, @creation_fields}
      {:error, reason} -> {:error, reason}
    end
  end

  defp update_ordinary(project, baseline, intended) do
    case Map.keys(intended) do
      [] -> current_or_not_found(project, baseline)
      fields -> update_ordinary_once(project, baseline, intended, fields)
    end
  end

  defp update_ordinary_once(project, baseline, intended, fields) do
    case persist_update(Task.changeset(baseline, intended)) do
      {:ok, persisted} -> {:ok, persisted, fields}
      {:error, changeset} -> {:error, changeset}
      :stale -> retry_ordinary(project, baseline, intended)
    end
  end

  defp retry_ordinary(project, baseline, intended) do
    case current_task(project, baseline) do
      nil ->
        {:error, :not_found}

      current ->
        case classify_intended_fields(baseline, current, intended) do
          {[_ | _] = conflicts, _remaining} ->
            {:error, %Conflict{task: current, fields: conflicts}}

          {[], remaining} when map_size(remaining) == 0 ->
            {:ok, current, []}

          {[], remaining} ->
            case persist_update(Task.changeset(current, remaining)) do
              {:ok, persisted} -> {:ok, persisted, ordered_fields(Map.keys(remaining))}
              {:error, changeset} -> {:error, changeset}
              :stale -> second_stale_conflict(project, current, Map.keys(remaining))
            end
        end
    end
  end

  defp update_with_parent(project, baseline, intended, parent) do
    intended = Map.put(intended, :parent_task_id, parent_id(parent))

    case parent_attempt(project, baseline, intended, parent, :initial) do
      :stale -> retry_parent_update(project, baseline, intended, parent)
      result -> result
    end
  end

  defp retry_parent_update(project, baseline, intended, parent) do
    case parent_attempt(project, baseline, intended, parent, :retry) do
      :stale ->
        case current_task(project, baseline) do
          nil ->
            {:error, :not_found}

          current ->
            {:error, %Conflict{task: current, fields: ordered_fields(Map.keys(intended))}}
        end

      result ->
        result
    end
  end

  defp parent_attempt(project, baseline, intended, parent, attempt) do
    case Repo.transaction(fn ->
           with %Project{} = locked_project <- lock_project(project),
                {:ok, current} <- current_task_for_attempt(locked_project, baseline, attempt),
                {:ok, persisted_parent} <- reload_parent(locked_project, parent),
                :ok <- Hierarchy.validate_parent(current, persisted_parent, locked_project) do
             parent_attempt_result(
               locked_project,
               baseline,
               current,
               intended,
               persisted_parent,
               attempt
             )
           else
             nil -> Repo.rollback(:not_found)
             {:error, :not_found} -> Repo.rollback(:not_found)
             {:error, :cycle} -> Repo.rollback(cycle_changeset(baseline, intended))
           end
         end) do
      {:ok, result} -> result
      {:error, reason} -> {:error, reason}
    end
  end

  defp parent_attempt_result(
         _locked_project,
         baseline,
         current,
         intended,
         _persisted_parent,
         :initial
       ) do
    changeset = parent_changeset(baseline, intended)
    fields = ordered_fields(Map.keys(changeset.changes))

    if fields == [] do
      initial_parent_noop_result(baseline, current, intended)
    else
      case persist_update(changeset) do
        {:ok, persisted} -> {:ok, persisted, fields}
        {:error, changeset} -> Repo.rollback(changeset)
        :stale -> :stale
      end
    end
  end

  defp parent_attempt_result(
         _locked_project,
         baseline,
         current,
         intended,
         _persisted_parent,
         :retry
       ) do
    case classify_intended_fields(baseline, current, intended) do
      {[_ | _] = conflicts, _remaining} ->
        Repo.rollback(%Conflict{task: current, fields: conflicts})

      {[], remaining} when map_size(remaining) == 0 ->
        {:ok, current, []}

      {[], remaining} ->
        changeset = parent_changeset(current, remaining)
        fields = ordered_fields(Map.keys(changeset.changes))

        case persist_update(changeset) do
          {:ok, persisted} -> {:ok, persisted, fields}
          {:error, changeset} -> Repo.rollback(changeset)
          :stale -> :stale
        end
    end
  end

  defp move_with_destination(project, baseline, destination_id) do
    if task_value(baseline, :list_id) == destination_id do
      case current_task(project, baseline) do
        nil ->
          {:error, :not_found}

        current ->
          if task_value(current, :list_id) == destination_id do
            {:error, :unchanged_location}
          else
            {:error, %Conflict{task: current, fields: [:list_id]}}
          end
      end
    else
      intended = %{list_id: destination_id}

      case persist_update(location_changeset(baseline, destination_id)) do
        {:ok, persisted} -> {:ok, persisted, [:list_id]}
        {:error, changeset} -> {:error, changeset}
        :stale -> retry_move(project, baseline, intended, destination_id)
      end
    end
  end

  defp retry_move(project, baseline, intended, destination_id) do
    with :ok <- destination(project, destination_id),
         %Task{} = current <- current_task(project, baseline) do
      case classify_intended_fields(baseline, current, intended) do
        {[_ | _] = conflicts, _remaining} ->
          {:error, %Conflict{task: current, fields: conflicts}}

        {[], remaining} when map_size(remaining) == 0 ->
          {:error, :unchanged_location}

        {[], _remaining} ->
          case persist_update(location_changeset(current, destination_id)) do
            {:ok, persisted} -> {:ok, persisted, [:list_id]}
            {:error, changeset} -> {:error, changeset}
            :stale -> second_stale_conflict(project, current, [:list_id])
          end
      end
    else
      nil -> {:error, :not_found}
      error -> error
    end
  end

  defp parent_changeset(task, intended) do
    {parent_id, ordinary} = Map.pop(intended, :parent_task_id)

    task
    |> Task.changeset(ordinary)
    |> Ecto.Changeset.put_change(:parent_task_id, parent_id)
  end

  defp initial_parent_noop_result(baseline, current, intended) do
    case classify_intended_fields(baseline, current, intended) do
      {[_ | _] = conflicts, _remaining} ->
        Repo.rollback(%Conflict{task: current, fields: conflicts})

      {[], _remaining} ->
        {:ok, current, []}
    end
  end

  defp location_changeset(task, destination_id) do
    task
    |> Task.changeset(%{})
    |> Ecto.Changeset.put_change(:list_id, destination_id)
  end

  defp persist_update(changeset) do
    try do
      case Repo.update(Ecto.Changeset.optimistic_lock(changeset, :lock_version)) do
        {:ok, task} -> {:ok, task}
        {:error, changeset} -> {:error, changeset}
      end
    rescue
      Ecto.StaleEntryError -> :stale
    end
  end

  defp second_stale_conflict(project, baseline, fields) do
    case current_task(project, baseline) do
      nil -> {:error, :not_found}
      current -> {:error, %Conflict{task: current, fields: ordered_fields(fields)}}
    end
  end

  defp current_or_not_found(project, baseline) do
    case current_task(project, baseline) do
      nil -> {:error, :not_found}
      current -> {:ok, current, []}
    end
  end

  defp current_task(%Project{id: project_id}, %Task{id: task_id}) when is_integer(task_id) do
    Repo.get_by(Task, id: task_id, project_id: project_id)
  end

  defp current_task(%Project{}, %Task{}), do: nil

  defp current_task_for_attempt(project, baseline, :initial) do
    case current_task(project, baseline) do
      nil -> {:error, :not_found}
      current -> {:ok, current}
    end
  end

  defp current_task_for_attempt(project, baseline, :retry),
    do: current_task_for_attempt(project, baseline, :initial)

  defp reload_parent(_project, nil), do: {:ok, nil}

  defp reload_parent(%Project{id: project_id}, %Task{id: parent_id}) when is_integer(parent_id) do
    case Repo.get_by(Task, id: parent_id, project_id: project_id) do
      nil -> {:error, :not_found}
      parent -> {:ok, parent}
    end
  end

  defp reload_parent(%Project{}, _parent), do: {:error, :not_found}

  defp destination(_project, nil), do: :ok

  defp destination(%Project{id: project_id}, destination_id) when is_integer(destination_id) do
    case Repo.get_by(TaskList, id: destination_id, project_id: project_id) do
      nil -> {:error, :not_found}
      _task_list -> :ok
    end
  end

  defp destination(%Project{}, _destination_id), do: {:error, :not_found}

  defp lock_project(%Project{id: project_id}) do
    Project
    |> where([project], project.id == ^project_id)
    |> lock("FOR UPDATE")
    |> Repo.one()
  end

  defp classify_intended_fields(baseline, current, intended) do
    Enum.reduce(intended, {[], %{}}, fn {field, requested}, {conflicts, remaining} ->
      baseline_value = task_value(baseline, field)
      current_value = task_value(current, field)

      cond do
        current_value == requested -> {conflicts, remaining}
        current_value == baseline_value -> {conflicts, Map.put(remaining, field, requested)}
        true -> {[field | conflicts], remaining}
      end
    end)
    |> then(fn {conflicts, remaining} -> {ordered_fields(conflicts), remaining} end)
  end

  defp task_value(task, field) do
    task
    |> Map.from_struct()
    |> Map.fetch!(field)
  end

  defp parent_id(nil), do: nil
  defp parent_id(%Task{id: id}), do: id
  defp parent_id(_parent), do: nil

  defp cycle_changeset(task, attrs) do
    task
    |> Task.changeset(attrs)
    |> Ecto.Changeset.add_error(:parent_task_id, "would create a cycle")
  end

  defp ordered_fields(fields) do
    fields
    |> Enum.uniq()
    |> Enum.sort_by(fn field ->
      case Enum.find_index(@field_order, &(&1 == field)) do
        nil -> {1, Atom.to_string(field)}
        index -> {0, index}
      end
    end)
  end
end
