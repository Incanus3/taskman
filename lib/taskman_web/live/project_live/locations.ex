defmodule TaskmanWeb.ProjectLive.Locations do
  @moduledoc "Page-local List metadata and prepared direct/subtree Task state availability."

  alias Taskman.Lists.TaskList
  alias Taskman.Tasks.Status

  @type location_id :: pos_integer() | nil
  @type entry :: %{list: TaskList.t() | nil, direct: MapSet.t(atom()), subtree: MapSet.t(atom())}
  @type project_locations :: %{
          loaded?: boolean(),
          entries: %{location_id() => entry()},
          pending: %{pos_integer() => MapSet.t(atom())}
        }
  @type t :: %__MODULE__{projects: %{pos_integer() => project_locations()}}
  defstruct projects: %{}

  @spec new() :: t()
  def new, do: %__MODULE__{}

  @spec loaded?(t(), pos_integer()) :: boolean()
  def loaded?(model, project_id), do: project(model, project_id).loaded?

  @doc "Merges a current snapshot with retained direct observations and authoritative topology."
  @spec load(t(), pos_integer(), [TaskList.t()], map()) :: t()
  def load(model, project_id, lists, observed_by_location) do
    model = reconcile_lists(model, project_id, lists)

    model =
      Enum.reduce(observed_by_location, model, fn {list_id, states}, acc ->
        Enum.reduce(states, acc, &observe(&2, project_id, list_id, &1))
      end)

    put_project(model, project_id, %{project(model, project_id) | loaded?: true})
  end

  @doc "Replaces canonical List metadata, materializing pending observations and pruning removed keys."
  @spec reconcile_lists(t(), pos_integer(), [TaskList.t()]) :: t()
  def reconcile_lists(model, project_id, lists) do
    current = project(model, project_id)

    entries =
      lists
      |> Enum.filter(&(&1.project_id == project_id))
      |> Map.new(fn task_list ->
        direct =
          current.entries
          |> Map.get(task_list.id, entry(nil))
          |> Map.fetch!(:direct)
          |> MapSet.union(Map.get(current.pending, task_list.id, MapSet.new()))

        {task_list.id, %{entry(task_list) | direct: direct}}
      end)
      |> Map.put(nil, Map.fetch!(current.entries, nil))
      |> rebuild()

    put_project(model, project_id, %{current | entries: entries, pending: %{}})
  end

  @doc "Adds supported additional-state use without inventing metadata for an unknown List."
  @spec observe(t(), term(), term(), term()) :: t()
  def observe(model, project_id, list_id, state)
      when is_integer(project_id) and project_id > 0 and
             (is_nil(list_id) or (is_integer(list_id) and list_id > 0)) do
    if state in Status.additional_values() do
      current = project(model, project_id)

      current =
        case Map.fetch(current.entries, list_id) do
          {:ok, entry} ->
            entries =
              Map.put(current.entries, list_id, %{entry | direct: MapSet.put(entry.direct, state)})

            %{current | entries: roll_up(entries, list_id, MapSet.new([state]), MapSet.new())}

          :error ->
            pending =
              Map.update(current.pending, list_id, MapSet.new([state]), &MapSet.put(&1, state))

            %{current | pending: pending}
        end

      put_project(model, project_id, current)
    else
      model
    end
  end

  def observe(model, _project_id, _list_id, _state), do: model

  @spec retain_projects(t(), [pos_integer()]) :: t()
  def retain_projects(model, project_ids),
    do: %{model | projects: Map.take(model.projects, project_ids)}

  @spec lists(t(), pos_integer()) :: [TaskList.t()]
  def lists(model, project_id) do
    model
    |> project(project_id)
    |> Map.fetch!(:entries)
    |> Map.delete(nil)
    |> Map.values()
    |> Enum.map(& &1.list)
    |> Enum.sort_by(&{DateTime.to_iso8601(&1.inserted_at), &1.id})
  end

  @doc "Reads a prepared scope in canonical state order without traversing child Lists."
  @spec available(t(), pos_integer(), pos_integer() | nil, :direct | :subtree) :: [atom()]
  def available(model, project_id, list_id, scope) when scope in [:direct, :subtree] do
    observed =
      model
      |> project(project_id)
      |> Map.fetch!(:entries)
      |> Map.get(list_id, entry(nil))
      |> Map.fetch!(scope)

    Enum.filter(Status.values(), &(&1 in Status.default_values() or MapSet.member?(observed, &1)))
  end

  defp project(model, id),
    do:
      Map.get(model.projects, id, %{loaded?: false, entries: %{nil => entry(nil)}, pending: %{}})

  defp entry(list), do: %{list: list, direct: MapSet.new(), subtree: MapSet.new()}

  defp put_project(model, id, project),
    do: %{model | projects: Map.put(model.projects, id, project)}

  defp rebuild(entries) do
    prepared = Map.new(entries, fn {id, entry} -> {id, %{entry | subtree: MapSet.new()}} end)

    Enum.reduce(entries, prepared, fn {id, entry}, acc ->
      roll_up(acc, id, entry.direct, MapSet.new())
    end)
  end

  defp roll_up(entries, id, states, visited) do
    if MapSet.member?(visited, id) or not Map.has_key?(entries, id) do
      entries
    else
      entry = Map.fetch!(entries, id)
      entries = Map.put(entries, id, %{entry | subtree: MapSet.union(entry.subtree, states)})

      case entry.list do
        nil -> entries
        list -> roll_up(entries, list.parent_list_id, states, MapSet.put(visited, id))
      end
    end
  end
end
