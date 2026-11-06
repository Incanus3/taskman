defmodule TaskmanWeb.ProjectLive.LocationsTest do
  use ExUnit.Case, async: true

  alias Taskman.Lists.TaskList
  alias TaskmanWeb.ProjectLive.Locations

  @defaults [:icebox, :pending, :in_progress, :in_review, :done, :will_not_do]
  @with_not_planned [
    :icebox,
    :pending,
    :in_progress,
    :in_review,
    :done,
    :not_planned,
    :will_not_do
  ]

  test "direct, ancestor and Project availability use distinct prepared scopes" do
    parent = task_list(10)
    child = task_list(11, 10)
    sibling = task_list(12)
    task_lists = [parent, child, sibling]
    model = Locations.load(Locations.new(), 1, task_lists, %{11 => MapSet.new([:not_planned])})

    assert Locations.loaded?(model, 1)
    refute Locations.loaded?(model, 2)
    assert Locations.lists(model, 1) == task_lists
    assert Locations.available(model, 1, 11, :direct) == @with_not_planned
    assert Locations.available(model, 1, 10, :direct) == @defaults
    assert Locations.available(model, 1, 10, :subtree) == @with_not_planned
    assert Locations.available(model, 1, 12, :subtree) == @defaults
    assert Locations.available(model, 1, nil, :direct) == @defaults
    assert Locations.available(model, 1, nil, :subtree) == @with_not_planned
  end

  test "canonical List order follows insertion time with ID ties" do
    older = task_list(20)
    later = %{task_list(10) | inserted_at: ~U[2026-02-01 00:00:00Z]}
    same_time = %{task_list(9) | inserted_at: later.inserted_at}
    model = Locations.load(Locations.new(), 1, [later, older, same_time], %{})
    assert Locations.lists(model, 1) == [older, same_time, later]
  end

  test "resnapshots retain direct observations and isolate Projects" do
    model =
      Locations.new()
      |> Locations.load(1, [task_list(10)], %{})
      |> Locations.observe(1, 10, :not_planned)
      |> Locations.observe(1, nil, :not_planned)
      |> Locations.load(2, [task_list(20, nil, 2)], %{})
      |> Locations.load(1, [task_list(10)], %{})

    assert Locations.available(model, 1, 10, :direct) == @with_not_planned
    assert Locations.available(model, 1, nil, :direct) == @with_not_planned
    assert Locations.available(model, 2, nil, :subtree) == @defaults
    assert Locations.available(model, 2, 10, :direct) == @defaults
    assert Locations.lists(Locations.retain_projects(model, [2]), 1) == []
    refute Locations.loaded?(Locations.retain_projects(model, [2]), 1)
  end

  test "reparenting rebuilds ancestors and authoritative removals prune observations" do
    child = task_list(11, 10)

    model =
      Locations.load(Locations.new(), 1, [task_list(10), child, task_list(12)], %{
        11 => [:not_planned]
      })

    moved = %{child | parent_list_id: 12, name: "Moved"}
    model = Locations.reconcile_lists(model, 1, [task_list(10), moved, task_list(12)])
    assert Locations.available(model, 1, 10, :subtree) == @defaults
    assert Locations.available(model, 1, 12, :subtree) == @with_not_planned
    assert Locations.available(model, 1, 11, :direct) == @with_not_planned
    assert Enum.find(Locations.lists(model, 1), &(&1.id == 11)) == moved

    model = Locations.reconcile_lists(model, 1, [task_list(10), task_list(12)])
    assert Locations.available(model, 1, nil, :subtree) == @defaults
    model = Locations.reconcile_lists(model, 1, [task_list(10), child, task_list(12)])
    assert Locations.available(model, 1, 11, :direct) == @defaults
  end

  test "unknown Lists remain pending until authoritative topology materializes them" do
    model =
      Locations.new()
      |> Locations.load(1, [task_list(10)], %{})
      |> Locations.observe(1, 11, :not_planned)
      |> Locations.observe(1, 99, :not_planned)

    assert Locations.lists(model, 1) == [task_list(10)]
    assert Locations.available(model, 1, nil, :subtree) == @defaults
    model = Locations.reconcile_lists(model, 1, [task_list(10), task_list(11, 10)])
    assert Locations.available(model, 1, 11, :direct) == @with_not_planned
    assert Locations.available(model, 1, 10, :subtree) == @with_not_planned
    assert Locations.available(model, 1, nil, :subtree) == @with_not_planned

    model = Locations.reconcile_lists(model, 1, [task_list(10), task_list(11, 10), task_list(99)])
    assert Locations.available(model, 1, 99, :direct) == @defaults
  end

  test "a fresh snapshot for a List arriving after its topology snapshot remains pending" do
    model = Locations.load(Locations.new(), 1, [task_list(10)], %{11 => [:not_planned]})
    assert Locations.lists(model, 1) == [task_list(10)]
    assert Locations.available(model, 1, nil, :subtree) == @defaults

    model = Locations.reconcile_lists(model, 1, [task_list(10), task_list(11, 10)])
    assert Locations.available(model, 1, 11, :direct) == @with_not_planned
    assert Locations.available(model, 1, nil, :subtree) == @with_not_planned
  end

  test "unsupported observations and foreign snapshot keys cannot broaden availability" do
    model =
      Locations.load(Locations.new(), 1, [task_list(10), task_list(20, nil, 2)], %{
        20 => [:not_planned],
        -1 => [:not_planned]
      })

    unchanged =
      Enum.reduce([:pending, :unknown, "not_planned", nil], model, fn state, acc ->
        Locations.observe(acc, 1, 10, state)
      end)
      |> Locations.observe(1, -1, :not_planned)
      |> Locations.observe(0, 10, :not_planned)

    assert unchanged == model
    assert Locations.lists(model, 1) == [task_list(10)]
    assert Locations.available(model, 1, nil, :subtree) == @defaults
  end

  defp task_list(id, parent_id \\ nil, project_id \\ 1) do
    %TaskList{
      id: id,
      project_id: project_id,
      parent_list_id: parent_id,
      name: "List #{id}",
      inserted_at: ~U[2026-01-01 00:00:00Z]
    }
  end
end
