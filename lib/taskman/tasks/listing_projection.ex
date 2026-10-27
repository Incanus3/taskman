defmodule Taskman.Tasks.ListingProjection do
  @moduledoc "Projects filtered Tasks into contextual roots and complete expanded subtrees."

  alias Taskman.Lists.TaskList
  alias Taskman.Projects.Project
  alias Taskman.Tasks
  alias Taskman.Tasks.ListingRow
  alias Taskman.Tasks.Task
  alias Taskman.Tasks.TaskWithLocation

  defstruct rows: [],
            expandable_root_ids: MapSet.new(),
            expanded_task_ids: MapSet.new(),
            matching_empty?: true,
            filtered_empty?: false

  @type t :: %__MODULE__{
          rows: [ListingRow.t()],
          expandable_root_ids: MapSet.t(pos_integer()),
          expanded_task_ids: MapSet.t(pos_integer()),
          matching_empty?: boolean(),
          filtered_empty?: boolean()
        }

  @spec for_location(Project.t(), TaskList.t() | nil, keyword()) ::
          {:ok, t()} | {:error, :not_found}
  def for_location(project, location, opts \\ []) do
    with {:ok, matching_tasks} <- Tasks.list_tasks_for_location(project, location, opts) do
      project_rows(project, location, matching_tasks, opts)
    end
  end

  defp project_rows(project, location, [], opts) do
    statuses = Keyword.get(opts, :statuses, Task.statuses())
    empty = %__MODULE__{expanded_task_ids: Keyword.get(opts, :expanded_task_ids, MapSet.new())}

    if Enum.all?(Task.statuses(), &(&1 in statuses)) do
      {:ok, empty}
    else
      with {:ok, all_status_tasks} <-
             Tasks.list_tasks_for_location(
               project,
               location,
               Keyword.put(opts, :statuses, Task.statuses())
             ) do
        {:ok, %{empty | filtered_empty?: all_status_tasks != []}}
      end
    end
  end

  defp project_rows(project, _location, matching_tasks, opts) do
    project_opts = [
      include_descendants: true,
      statuses: Task.statuses(),
      sort: Keyword.get(opts, :sort)
    ]

    with {:ok, ordered_project_tasks} <-
           Tasks.list_tasks_for_location(project, nil, project_opts) do
      {:ok,
       from_tasks(
         matching_tasks,
         ordered_project_tasks,
         Keyword.get(opts, :expanded_task_ids, MapSet.new()),
         opts
       )}
    end
  end

  @doc "Builds rows from a filtered result and every Project Task in the requested order."
  @spec from_tasks(
          [TaskWithLocation.t()],
          [TaskWithLocation.t()],
          MapSet.t(pos_integer()),
          keyword()
        ) ::
          t()
  def from_tasks(matching_tasks, ordered_project_tasks, expanded_task_ids, opts \\ [])

  def from_tasks([], _ordered_project_tasks, expanded_task_ids, _opts),
    do: %__MODULE__{expanded_task_ids: expanded_task_ids}

  def from_tasks(matching_tasks, ordered_project_tasks, expanded_task_ids, opts) do
    roots = contextual_roots(matching_tasks, ordered_project_tasks)
    children = Enum.group_by(ordered_project_tasks, & &1.task.parent_task_id)

    branch_ids =
      ordered_project_tasks
      |> Enum.filter(&Map.has_key?(children, &1.task.id))
      |> MapSet.new(& &1.task.id)

    expandable_ids = roots |> MapSet.new(& &1.task.id) |> MapSet.intersection(branch_ids)

    expanded_ids =
      expanded_task_ids
      |> MapSet.intersection(branch_ids)
      |> toggle_branch(Keyword.get(opts, :toggle_task_id), expandable_ids, children)

    rows = Enum.flat_map(roots, &root_rows(&1, children, expandable_ids, expanded_ids))

    remembered_ids =
      Enum.reduce(rows, expanded_ids, fn row, remembered ->
        id = row.task_with_location.task.id

        if row.depth > 0 and MapSet.member?(branch_ids, id),
          do: MapSet.put(remembered, id),
          else: remembered
      end)

    %__MODULE__{
      rows: rows,
      expandable_root_ids: expandable_ids,
      expanded_task_ids: remembered_ids,
      matching_empty?: false
    }
  end

  defp toggle_branch(expanded_ids, id, expandable_ids, children) do
    cond do
      not MapSet.member?(expandable_ids, id) ->
        expanded_ids

      MapSet.member?(expanded_ids, id) ->
        pending = Enum.map(Map.fetch!(children, id), &{&1, 1})
        descendants = descendant_rows(pending, children, [])
        forgotten_ids = MapSet.new(descendants, & &1.task_with_location.task.id)
        MapSet.difference(expanded_ids, MapSet.put(forgotten_ids, id))

      true ->
        MapSet.put(expanded_ids, id)
    end
  end

  defp contextual_roots(matching_tasks, ordered_project_tasks) do
    matching_ids = MapSet.new(matching_tasks, & &1.task.id)
    parents = Map.new(ordered_project_tasks, &{&1.task.id, &1.task.parent_task_id})

    {roots, _memo} =
      Enum.reduce(ordered_project_tasks, {[], %{}}, fn listed_task, {roots, memo} ->
        if MapSet.member?(matching_ids, listed_task.task.id) do
          {ancestor_matches?, memo} =
            matching_ancestor?(listed_task.task.id, matching_ids, parents, memo)

          roots = if ancestor_matches?, do: roots, else: [listed_task | roots]
          {roots, memo}
        else
          {roots, memo}
        end
      end)

    Enum.reverse(roots)
  end

  defp root_rows(listed_task, children, expandable_ids, expanded_ids) do
    id = listed_task.task.id
    expanded? = MapSet.member?(expanded_ids, id)

    row = %ListingRow{
      task_with_location: listed_task,
      expandable?: MapSet.member?(expandable_ids, id),
      expanded?: expanded?
    }

    if expanded? do
      pending = Enum.map(Map.fetch!(children, id), &{&1, 1})
      [row | descendant_rows(pending, children, [])]
    else
      [row]
    end
  end

  defp matching_ancestor?(id, matching_ids, parents, memo) do
    case Map.fetch(memo, id) do
      {:ok, result} ->
        {result, memo}

      :error ->
        parent_id = Map.get(parents, id)

        {result, memo} =
          cond do
            is_nil(parent_id) -> {false, memo}
            MapSet.member?(matching_ids, parent_id) -> {true, memo}
            true -> matching_ancestor?(parent_id, matching_ids, parents, memo)
          end

        {result, Map.put(memo, id, result)}
    end
  end

  defp descendant_rows([], _children, rows), do: Enum.reverse(rows)

  defp descendant_rows([{listed_task, depth} | rest], children, rows) do
    row = %ListingRow{task_with_location: listed_task, depth: depth}

    pending =
      children
      |> Map.get(listed_task.task.id, [])
      |> Enum.reverse()
      |> Enum.reduce(rest, fn child, pending ->
        [{child, depth + 1} | pending]
      end)

    descendant_rows(pending, children, [row | rows])
  end
end
