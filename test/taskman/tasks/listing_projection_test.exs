defmodule Taskman.Tasks.ListingProjectionTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks

  test "remembered branches survive promotion and returning ancestors" do
    project = project_fixture(%{})
    a = task_fixture(project, %{status: :pending})
    b = task_fixture(project, %{status: :in_review}, parent: a)
    c = task_fixture(project, %{status: :done}, parent: b)

    assert {:ok, expanded} =
             Tasks.list_task_rows_for_location(project, nil,
               statuses: [:pending, :in_review],
               expanded_task_ids: MapSet.new([a.id])
             )

    assert {:ok, promoted} =
             Tasks.list_task_rows_for_location(project, nil,
               statuses: [:in_review],
               expanded_task_ids: expanded.expanded_task_ids
             )

    assert row_depths(promoted) == [{b.id, 0}, {c.id, 1}]
    assert promoted.expanded_task_ids == MapSet.new([a.id, b.id])

    assert {:ok, restored} =
             Tasks.list_task_rows_for_location(project, nil,
               statuses: [:pending, :in_review],
               expanded_task_ids: promoted.expanded_task_ids
             )

    assert row_depths(restored) == [{a.id, 0}, {b.id, 1}, {c.id, 2}]
  end

  test "collapse forgets the branch and restoration relearns it" do
    project = project_fixture(%{})
    a = task_fixture(project, %{status: :pending})
    b = task_fixture(project, %{status: :in_review}, parent: a)
    c = task_fixture(project, %{status: :done}, parent: b)
    d = task_fixture(project, %{status: :done}, parent: c)
    memory = MapSet.new([a.id, b.id, c.id])

    assert {:ok, collapsed} =
             Tasks.list_task_rows_for_location(project, nil,
               statuses: [:in_review],
               expanded_task_ids: memory,
               toggle_task_id: b.id
             )

    assert row_depths(collapsed) == [{b.id, 0}]
    assert collapsed.expanded_task_ids == MapSet.new([a.id])

    assert {:ok, restored} =
             Tasks.list_task_rows_for_location(project, nil,
               statuses: [:pending, :in_review],
               expanded_task_ids: collapsed.expanded_task_ids
             )

    assert row_depths(restored) == [{a.id, 0}, {b.id, 1}, {c.id, 2}, {d.id, 3}]
    assert restored.expanded_task_ids == memory

    assert {:ok, collapsed} =
             Tasks.list_task_rows_for_location(project, nil,
               expanded_task_ids: restored.expanded_task_ids,
               toggle_task_id: a.id
             )

    assert row_depths(collapsed) == [{a.id, 0}]
    assert collapsed.expanded_task_ids == MapSet.new()

    assert {:ok, promoted} =
             Tasks.list_task_rows_for_location(project, nil,
               statuses: [:in_review],
               expanded_task_ids: collapsed.expanded_task_ids
             )

    assert row_depths(promoted) == [{b.id, 0}]
    assert promoted.expanded_task_ids == MapSet.new()
  end

  test "collapse preserves unrelated remembered branches" do
    project = project_fixture(%{})
    a = task_fixture(project)
    b = task_fixture(project, %{}, parent: a)
    task_fixture(project, %{}, parent: b)
    d = task_fixture(project, %{}, parent: a)
    task_fixture(project, %{}, parent: d)
    x = task_fixture(project)
    y = task_fixture(project, %{}, parent: x)

    assert {:ok, expanded} =
             Tasks.list_task_rows_for_location(project, nil,
               expanded_task_ids: MapSet.new([a.id, x.id])
             )

    assert expanded.expanded_task_ids == MapSet.new([a.id, b.id, d.id, x.id])

    assert {:ok, collapsed} =
             Tasks.list_task_rows_for_location(project, nil,
               expanded_task_ids: expanded.expanded_task_ids,
               toggle_task_id: a.id
             )

    assert collapsed.expanded_task_ids == MapSet.new([x.id])
    assert row_depths(collapsed) == [{a.id, 0}, {x.id, 0}, {y.id, 1}]
  end

  test "fresh roots validate disclosure intent" do
    project = project_fixture(%{})
    a = task_fixture(project, %{status: :pending})
    b = task_fixture(project, %{status: :in_review}, parent: a)
    c = task_fixture(project, %{status: :done}, parent: b)
    d = task_fixture(project, %{status: :done}, parent: c)
    leaf = task_fixture(project, %{status: :in_review})
    foreign = task_fixture(project_fixture(%{}))
    memory = MapSet.new([a.id, b.id])

    for target <- [a.id, c.id, leaf.id, foreign.id, 999_999_999] do
      assert {:ok, projection} =
               Tasks.list_task_rows_for_location(project, nil,
                 statuses: [:in_review],
                 expanded_task_ids: MapSet.put(memory, foreign.id),
                 toggle_task_id: target
               )

      assert projection.expanded_task_ids == MapSet.new([a.id, b.id, c.id])
      assert Enum.take(row_depths(projection), 3) == [{b.id, 0}, {c.id, 1}, {d.id, 2}]
    end

    assert {:ok, nested_intent} =
             Tasks.list_task_rows_for_location(project, nil,
               statuses: [:pending],
               expanded_task_ids: MapSet.new([b.id]),
               toggle_task_id: b.id
             )

    assert row_depths(nested_intent) == [{a.id, 0}]
    assert nested_intent.expanded_task_ids == MapSet.new([b.id])
  end

  test "empty matches preserve memory until hierarchy validation" do
    project = project_fixture(%{})
    a = task_fixture(project)
    b = task_fixture(project, %{}, parent: a)
    child = task_fixture(project, %{}, parent: b)
    memory = MapSet.new([a.id, b.id, 999_999_999])
    Taskman.Repo.delete!(child)

    assert {:ok, empty} =
             Tasks.list_task_rows_for_location(project, nil,
               statuses: [],
               expanded_task_ids: memory,
               toggle_task_id: a.id
             )

    assert empty.rows == []
    assert empty.matching_empty?
    assert empty.filtered_empty?
    assert empty.expanded_task_ids == memory

    assert {:ok, restored} =
             Tasks.list_task_rows_for_location(project, nil,
               expanded_task_ids: empty.expanded_task_ids
             )

    assert restored.expanded_task_ids == MapSet.new([a.id])

    Taskman.Repo.delete!(b)

    assert {:ok, restored} =
             Tasks.list_task_rows_for_location(project, nil,
               expanded_task_ids: restored.expanded_task_ids
             )

    assert restored.expanded_task_ids == MapSet.new()
  end

  test "revealed branches acquire bounded memory" do
    project = project_fixture(%{})
    a = task_fixture(project)
    b = task_fixture(project, %{}, parent: a)
    x = task_fixture(project)
    y = task_fixture(project, %{}, parent: x)
    task_fixture(project, %{}, parent: y)
    task_fixture(project, %{}, parent: x)

    assert {:ok, initial} =
             Tasks.list_task_rows_for_location(project, nil,
               expanded_task_ids: MapSet.new([a.id])
             )

    assert initial.expanded_task_ids == MapSet.new([a.id])
    task_fixture(project, %{}, parent: b)
    {:ok, y} = Tasks.update_task(project, y, %{}, parent: a)

    Enum.reduce(1..3, initial.expanded_task_ids, fn _, memory ->
      assert {:ok, projection} =
               Tasks.list_task_rows_for_location(project, nil, expanded_task_ids: memory)

      assert projection.expanded_task_ids == MapSet.new([a.id, b.id, y.id])
      assert MapSet.subset?(projection.expanded_task_ids, MapSet.new([a.id, b.id, x.id, y.id]))
      refute MapSet.member?(projection.expanded_task_ids, x.id)
      projection.expanded_task_ids
    end)
  end

  test "a matching ancestor suppresses descendants across a filtered gap" do
    project = project_fixture(%{})
    list = list_fixture(project)
    a = task_fixture(project, list, %{title: "A", status: :pending})
    b = task_fixture(project, %{title: "B", status: :done}, parent: a)
    c = task_fixture(project, list, %{title: "C", status: :pending}, parent: b)
    opts = [statuses: [:pending]]

    assert {:ok, collapsed} = Tasks.list_task_rows_for_location(project, list, opts)
    assert Enum.map(collapsed.rows, & &1.task_with_location.task.id) == [a.id]
    assert collapsed.expandable_root_ids == MapSet.new([a.id])

    assert {:ok, expanded} =
             Tasks.list_task_rows_for_location(
               project,
               list,
               Keyword.put(opts, :expanded_task_ids, MapSet.new([a.id]))
             )

    assert Enum.map(expanded.rows, &{&1.task_with_location.task.id, &1.depth}) ==
             [{a.id, 0}, {b.id, 1}, {c.id, 2}]

    assert expanded.expanded_task_ids == MapSet.new([a.id, b.id])
  end

  test "roots follow complete location and status filters" do
    project = project_fixture(%{})
    list = list_fixture(project)
    nested = list_fixture(project, list)
    sibling = list_fixture(project)
    direct = task_fixture(project, %{status: :pending})
    outside = task_fixture(project, sibling, %{status: :done})
    contextual = task_fixture(project, list, %{status: :pending}, parent: outside)
    root = task_fixture(project, list, %{status: :pending})
    gap = task_fixture(project, sibling, %{status: :done}, parent: root)
    task_fixture(project, nested, %{status: :pending}, parent: gap)
    child_list_root = task_fixture(project, nested, %{status: :pending})
    task_fixture(project_fixture(%{}), %{status: :pending})

    for {location, include?, expected} <- [
          {nil, false, [direct.id]},
          {nil, true, [direct.id, contextual.id, root.id, child_list_root.id]},
          {list, false, [contextual.id, root.id]},
          {list, true, [contextual.id, root.id, child_list_root.id]}
        ] do
      assert {:ok, projection} =
               Tasks.list_task_rows_for_location(project, location,
                 include_descendants: include?,
                 statuses: [:pending]
               )

      assert row_depths(projection) == Enum.map(expected, &{&1, 0})
      refute projection.matching_empty?
      refute projection.filtered_empty?
    end

    assert {:ok, projection} =
             Tasks.list_task_rows_for_location(project, nil,
               include_descendants: true,
               statuses: [:pending, :done]
             )

    assert row_depths(projection) ==
             Enum.map([direct.id, outside.id, root.id, child_list_root.id], &{&1, 0})
  end

  test "subtrees include every excluded intermediate and descendant" do
    project = project_fixture(%{})
    selected = list_fixture(project, %{name: "Selected"})
    sibling = list_fixture(project, %{name: "Sibling"})
    nested = list_fixture(project, sibling, %{name: "Nested"})
    root = task_fixture(project, selected, %{status: :pending})
    closed = task_fixture(project, sibling, %{status: :done}, parent: root)
    declined = task_fixture(project, nested, %{status: :will_not_do}, parent: closed)
    direct = task_fixture(project, %{status: :icebox}, parent: root)
    leaf = task_fixture(project, selected, %{status: :pending})

    assert {:ok, projection} =
             Tasks.list_task_rows_for_location(project, selected,
               statuses: [:pending],
               expanded_task_ids: MapSet.new([root.id])
             )

    assert row_depths(projection) ==
             [{root.id, 0}, {closed.id, 1}, {declined.id, 2}, {direct.id, 1}, {leaf.id, 0}]

    assert Enum.map(projection.rows, &{&1.expandable?, &1.expanded?}) ==
             [{true, true}, {false, false}, {false, false}, {false, false}, {false, false}]

    assert Enum.map(
             projection.rows,
             &{&1.task_with_location.task.status,
              Enum.map(&1.task_with_location.location_path, fn list -> list.id end)}
           ) ==
             [
               {:pending, [selected.id]},
               {:done, [sibling.id]},
               {:will_not_do, [sibling.id, nested.id]},
               {:icebox, []},
               {:pending, [selected.id]}
             ]
  end

  test "each sibling level follows existing column order" do
    project = project_fixture(%{})
    alpha = list_fixture(project, %{name: "Alpha"})
    zulu = list_fixture(project, %{name: "zulu"})
    first = task_fixture(project, zulu, %{title: "beta", status: :done, priority: :low})
    second = task_fixture(project, alpha, %{title: "Alpha", status: :pending, priority: :urgent})
    third = task_fixture(project, alpha, %{title: "alpha", status: :pending, priority: :urgent})

    child_a =
      task_fixture(project, zulu, %{title: "beta", status: :will_not_do, priority: :low},
        parent: first
      )

    child_b =
      task_fixture(project, alpha, %{title: "Alpha", status: :done, priority: :medium},
        parent: first
      )

    child_c =
      task_fixture(project, alpha, %{title: "alpha", status: :done, priority: :medium},
        parent: first
      )

    grandchild_a =
      task_fixture(project, alpha, %{title: "beta", status: :pending, priority: :urgent},
        parent: child_a
      )

    grandchild_b =
      task_fixture(project, zulu, %{title: "Alpha", status: :in_review, priority: :none},
        parent: child_a
      )

    grandchild_c =
      task_fixture(project, zulu, %{title: "alpha", status: :in_review, priority: :none},
        parent: child_a
      )

    for sort <- [
          nil
          | for(
              field <- [:id, :title, :status, :priority, :location],
              direction <- [:asc, :desc],
              do: {field, direction}
            )
        ] do
      opts = [include_descendants: true, sort: sort]
      assert {:ok, flat} = Tasks.list_tasks_for_location(project, nil, opts)

      assert {:ok, projection} =
               Tasks.list_task_rows_for_location(
                 project,
                 nil,
                 Keyword.put(opts, :expanded_task_ids, MapSet.new([first.id]))
               )

      groups = [
        [first.id, second.id, third.id],
        [child_a.id, child_b.id, child_c.id],
        [grandchild_a.id, grandchild_b.id, grandchild_c.id]
      ]

      for {ids, depth} <- Enum.with_index(groups) do
        expected = flat |> Enum.map(& &1.task.id) |> Enum.filter(&(&1 in ids))

        actual =
          projection.rows
          |> Enum.filter(&(&1.depth == depth))
          |> Enum.map(& &1.task_with_location.task.id)

        assert actual == expected
      end

      ids = Enum.map(projection.rows, & &1.task_with_location.task.id)
      child_index = Enum.find_index(ids, &(&1 == child_a.id))

      assert Enum.slice(ids, child_index + 1, 3) |> MapSet.new() ==
               MapSet.new([grandchild_a.id, grandchild_b.id, grandchild_c.id])

      assert length(ids) == length(Enum.uniq(ids))
    end
  end

  test "default order uses creation time then ID at each sibling level" do
    project = project_fixture(%{})
    first = task_fixture(project)
    second = task_fixture(project)
    child_a = task_fixture(project, %{}, parent: first)
    child_b = task_fixture(project, %{}, parent: first)
    import Ecto.Query
    Taskman.Repo.update_all(Taskman.Tasks.Task, set: [inserted_at: ~U[2026-01-01 00:00:00Z]])

    assert {:ok, projection} =
             Tasks.list_task_rows_for_location(project, nil,
               expanded_task_ids: MapSet.new([first.id])
             )

    assert row_depths(projection) == [
             {first.id, 0},
             {child_a.id, 1},
             {child_b.id, 1},
             {second.id, 0}
           ]

    from(task in Taskman.Tasks.Task, where: task.id in ^[second.id, child_b.id])
    |> Taskman.Repo.update_all(set: [inserted_at: ~U[2025-01-01 00:00:00Z]])

    assert {:ok, projection} =
             Tasks.list_task_rows_for_location(project, nil,
               expanded_task_ids: MapSet.new([first.id])
             )

    assert row_depths(projection) == [
             {second.id, 0},
             {first.id, 0},
             {child_b.id, 1},
             {child_a.id, 1}
           ]
  end

  test "prunes expansion to current Project branches" do
    project = project_fixture(%{})
    root = task_fixture(project)
    child = task_fixture(project, %{}, parent: root)
    task_fixture(project, %{}, parent: child)
    leaf = task_fixture(project)
    foreign = task_fixture(project_fixture(%{}))
    requested = MapSet.new([root.id, child.id, leaf.id, foreign.id, 999_999_999])

    assert {:ok, projection} =
             Tasks.list_task_rows_for_location(project, nil, expanded_task_ids: requested)

    assert projection.expandable_root_ids == MapSet.new([root.id])
    assert projection.expanded_task_ids == MapSet.new([root.id, child.id])
    ids = Enum.map(projection.rows, & &1.task_with_location.task.id)
    assert length(ids) == 4
    assert length(Enum.uniq(ids)) == 4
  end

  test "empty filters and unavailable Lists have existing semantics" do
    project = project_fixture(%{})
    empty = list_fixture(project)
    occupied = list_fixture(project)
    task_fixture(project, occupied, %{status: :done})

    for {location, statuses, filtered?} <- [
          {empty, Taskman.Tasks.Task.statuses(), false},
          {empty, [], false},
          {empty, [:pending], false},
          {occupied, [], true},
          {occupied, [:pending], true},
          {occupied, ["done", :unknown, nil], true},
          {nil, [], false}
        ] do
      assert {:ok, projection} =
               Tasks.list_task_rows_for_location(project, location,
                 statuses: statuses,
                 expanded_task_ids: MapSet.new([999_999_999])
               )

      assert projection.rows == []
      assert projection.expandable_root_ids == MapSet.new()
      assert projection.expanded_task_ids == MapSet.new([999_999_999])
      assert projection.matching_empty?
      assert projection.filtered_empty? == filtered?
    end

    assert {:ok, projection} =
             Tasks.list_task_rows_for_location(project, occupied,
               statuses: [:unknown, "done", :done]
             )

    refute projection.matching_empty?
    refute projection.filtered_empty?
    missing = %{occupied | id: 999_999_999}
    foreign = list_fixture(project_fixture(%{}))
    assert Tasks.list_task_rows_for_location(project, missing) == {:error, :not_found}
    assert Tasks.list_task_rows_for_location(project, foreign) == {:error, :not_found}
  end

  test "hierarchy loading uses bounded batch queries" do
    project = project_fixture(%{})
    list = list_fixture(project)
    root = task_fixture(project, list, %{})
    single_queries = listing_queries(project, list, [root.id])
    roots = for _ <- 1..15, do: task_fixture(project, list, %{})
    many_queries = listing_queries(project, list, Enum.map([root | roots], & &1.id))
    Enum.reduce(1..35, root, fn _, parent -> task_fixture(project, %{}, parent: parent) end)
    deep_queries = listing_queries(project, list, [root.id])
    assert length(single_queries) == length(many_queries)
    assert length(single_queries) == length(deep_queries)
    assert length(deep_queries) <= 4
    assert length(deep_queries) > 0

    assert Enum.all?(
             deep_queries,
             &(String.contains?(&1, "FROM \"tasks\"") or
                 String.contains?(&1, "FROM \"lists\""))
           )
  end

  test "remembered branches and disclosure reuse the same bounded reads" do
    project = project_fixture(%{})
    selected = list_fixture(project)
    a = task_fixture(project, selected, %{status: :in_review})
    b = task_fixture(project, %{status: :pending}, parent: a)
    task_fixture(project, %{}, parent: b)
    opts = [include_descendants: true]
    refresh_queries = listing_queries(project, nil, [], opts)

    for _ <- 1..3 do
      for {memory, statuses, toggle} <- [
            {[], [:pending, :in_review], a.id},
            {[a.id, b.id], [:pending, :in_review], a.id},
            {[a.id, b.id], [:pending], nil},
            {[a.id, b.id], [:pending, :in_review], nil}
          ] do
        queries =
          listing_queries(
            project,
            nil,
            memory,
            opts ++ [statuses: statuses, toggle_task_id: toggle]
          )

        assert length(queries) == length(refresh_queries)
      end
    end

    Enum.reduce(1..20, b, fn _, parent -> task_fixture(project, %{}, parent: parent) end)

    assert length(listing_queries(project, nil, [a.id, b.id], opts ++ [toggle_task_id: a.id])) ==
             length(refresh_queries)

    empty_queries = listing_queries(project, selected, [], statuses: [])

    remembered_empty_queries =
      listing_queries(project, selected, [a.id, b.id], statuses: [], toggle_task_id: a.id)

    assert remembered_empty_queries == empty_queries
    task_reads = Enum.filter(empty_queries, &String.contains?(&1, "FROM \"tasks\""))
    assert task_reads != []
    assert Enum.all?(task_reads, &String.contains?(&1, ~s|AND (t0."list_id" = $2)|))
  end

  test "pure projection uses full ancestor membership and preordered children" do
    alias Taskman.Tasks.{ListingProjection, Task, TaskWithLocation}

    make_task = fn id, parent ->
      %TaskWithLocation{task: %Task{id: id, parent_task_id: parent}, location_path: []}
    end

    a = make_task.(1, nil)
    b = make_task.(2, 1)
    c = make_task.(3, 2)
    d = make_task.(4, 1)
    projection = ListingProjection.from_tasks([c, a], [d, c, b, a], MapSet.new([1, 3]))
    assert row_depths(projection) == [{1, 0}, {4, 1}, {2, 1}, {3, 2}]
    assert projection.expandable_root_ids == MapSet.new([1])
    assert projection.expanded_task_ids == MapSet.new([1, 2])
    refute projection.matching_empty?
    refute projection.filtered_empty?
    empty = ListingProjection.from_tasks([], [a, b], MapSet.new([1, 99]), toggle_task_id: 1)
    assert empty.matching_empty?
    refute empty.filtered_empty?
    assert empty.rows == []
    assert empty.expandable_root_ids == MapSet.new()
    assert empty.expanded_task_ids == MapSet.new([1, 99])
  end

  test "tree guides follow sibling endings and only continuing ancestors" do
    alias Taskman.Tasks.{ListingProjection, Task, TaskWithLocation}

    make_task = fn id, parent ->
      %TaskWithLocation{task: %Task{id: id, parent_task_id: parent}, location_path: []}
    end

    a = make_task.(1, nil)
    b = make_task.(2, 1)
    c = make_task.(3, 2)
    d = make_task.(4, 1)
    e = make_task.(5, nil)
    f = make_task.(6, 4)
    tasks = [a, b, c, d, e, f]

    projection = ListingProjection.from_tasks(tasks, tasks, MapSet.new([1]))

    assert guide_branches(projection) == [
             {1, 0, true, [], true},
             {2, 1, false, [], true},
             {3, 2, true, [1], false},
             {4, 1, true, [], true},
             {6, 2, true, [], false},
             {5, 0, true, [], false}
           ]

    reordered = ListingProjection.from_tasks(tasks, [a, d, f, b, c, e], MapSet.new([1]))

    assert guide_branches(reordered) == [
             {1, 0, true, [], true},
             {4, 1, false, [], true},
             {6, 2, true, [1], false},
             {2, 1, true, [], true},
             {3, 2, true, [], false},
             {5, 0, true, [], false}
           ]

    promoted = ListingProjection.from_tasks([b, d], tasks, projection.expanded_task_ids)

    assert guide_branches(promoted) == [
             {2, 0, true, [], true},
             {3, 1, true, [], false},
             {4, 0, true, [], true},
             {6, 1, true, [], false}
           ]

    collapsed = ListingProjection.from_tasks(tasks, tasks, MapSet.new())
    assert guide_branches(collapsed) == [{1, 0, true, [], false}, {5, 0, true, [], false}]
  end

  test "tree guide ancestry retains actual depths beyond the visible indentation budget" do
    alias Taskman.Tasks.{ListingProjection, Task, TaskWithLocation}

    chain =
      for id <- 1..9 do
        %TaskWithLocation{
          task: %Task{id: id, parent_task_id: if(id == 1, do: nil, else: id - 1)},
          location_path: []
        }
      end

    siblings =
      for id <- 2..8 do
        %TaskWithLocation{task: %Task{id: id + 10, parent_task_id: id - 1}, location_path: []}
      end

    tasks = chain ++ siblings
    projection = ListingProjection.from_tasks(tasks, tasks, MapSet.new([1]))
    deepest = Enum.find(projection.rows, &(&1.task_with_location.task.id == 9))
    assert deepest.depth == 8
    assert Map.get(deepest, :ancestor_continuations) == [1, 2, 3, 4, 5, 6, 7]
    assert Map.get(deepest, :last_sibling?) == true
  end

  defp guide_branches(projection) do
    Enum.map(projection.rows, fn row ->
      {row.task_with_location.task.id, row.depth, Map.get(row, :last_sibling?),
       Map.get(row, :ancestor_continuations), Map.get(row, :children_visible?)}
    end)
  end

  defp row_depths(projection) do
    Enum.map(projection.rows, &{&1.task_with_location.task.id, &1.depth})
  end

  defp listing_queries(project, location, expanded_ids, opts \\ []) do
    owner = self()
    handler = {__MODULE__, make_ref()}

    :ok =
      :telemetry.attach(
        handler,
        [:taskman, :repo, :query],
        fn _, _, metadata, pid ->
          if self() == pid and String.match?(metadata.query, ~r/^(SELECT|WITH)/),
            do: send(pid, {:listing_query, metadata.query})
        end,
        owner
      )

    on_exit(fn -> :telemetry.detach(handler) end)

    try do
      assert {:ok, _} =
               Tasks.list_task_rows_for_location(
                 project,
                 location,
                 Keyword.put(opts, :expanded_task_ids, MapSet.new(expanded_ids))
               )

      collect_queries([])
    after
      :telemetry.detach(handler)
    end
  end

  defp collect_queries(queries) do
    receive do
      {:listing_query, query} -> collect_queries([query | queries])
    after
      0 -> Enum.reverse(queries)
    end
  end
end
