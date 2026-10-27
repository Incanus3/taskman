defmodule Taskman.Tasks.Counts do
  @moduledoc false

  alias Taskman.Projects.Project
  alias Taskman.Tasks.CountPersistence
  alias Taskman.Tasks.Task

  @unclosed_statuses [:icebox, :pending, :in_progress, :in_review]
  @closed_statuses [:done, :will_not_do]

  @type pair :: %{visible: non_neg_integer(), eligible: non_neg_integer()}
  @type t :: %{(pos_integer() | nil) => pair()}

  @spec for_project(Project.t(), [term()], boolean()) :: t()
  def for_project(%Project{id: project_id}, visible_statuses, include_descendants?) do
    visible_statuses = Enum.filter(visible_statuses, &(is_atom(&1) and &1 in Task.statuses()))

    eligible_statuses =
      @unclosed_statuses ++ Enum.filter(@closed_statuses, &(&1 in visible_statuses))

    project_id
    |> CountPersistence.list_location_counts(visible_statuses, eligible_statuses)
    |> from_rows(include_descendants?)
  end

  @spec from_rows([CountPersistence.row()], boolean()) :: t()
  def from_rows(rows, include_descendants?) do
    counts =
      Map.new(rows, fn row ->
        {row.list_id, %{visible: row.visible_count, eligible: row.eligible_count}}
      end)

    if include_descendants? do
      # The null-ID Project row terminates the tree; only Lists have parents.
      parents =
        for %{list_id: id, parent_list_id: parent_id} <- rows,
            not is_nil(id),
            into: %{},
            do: {id, parent_id}

      remaining_children =
        Enum.reduce(parents, Map.new(rows, &{&1.list_id, 0}), fn {_id, parent_id}, acc ->
          Map.update!(acc, parent_id, &(&1 + 1))
        end)

      leaves = for {id, 0} <- remaining_children, do: id
      roll_up(leaves, parents, remaining_children, counts)
    else
      counts
    end
  end

  defp roll_up([], _parents, _remaining_children, counts), do: counts

  defp roll_up([nil | leaves], parents, remaining_children, counts),
    do: roll_up(leaves, parents, remaining_children, counts)

  defp roll_up([id | leaves], parents, remaining_children, counts) do
    parent_id = Map.fetch!(parents, id)
    child = Map.fetch!(counts, id)

    counts =
      Map.update!(counts, parent_id, fn parent ->
        %{visible: parent.visible + child.visible, eligible: parent.eligible + child.eligible}
      end)

    remaining_children = Map.update!(remaining_children, parent_id, &(&1 - 1))

    leaves =
      if Map.fetch!(remaining_children, parent_id) == 0,
        do: [parent_id | leaves],
        else: leaves

    roll_up(leaves, parents, remaining_children, counts)
  end
end
