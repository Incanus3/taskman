defmodule Taskman.Tasks.CountsTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.Counts

  @unclosed [:icebox, :pending, :in_progress, :in_review]

  for {label, statuses, expected} <- [
        {"unclosed", @unclosed, %{visible: 10, eligible: 10}},
        {"review", [:in_review], %{visible: 1, eligible: 10}},
        {"review and done", [:in_review, :done], %{visible: 6, eligible: 15}},
        {"will not do", [:will_not_do], %{visible: 6, eligible: 16}},
        {"closed", [:done, :will_not_do], %{visible: 11, eligible: 21}},
        {"all", @unclosed ++ [:done, :will_not_do], %{visible: 21, eligible: 21}},
        {"none", [], %{visible: 0, eligible: 10}},
        {"unsupported only", [:unknown, "pending", "done", nil, 7], %{visible: 0, eligible: 10}},
        {"mixed with closed", [:unknown, "pending", :in_review, :done, "will_not_do"],
         %{visible: 6, eligible: 15}}
      ] do
    test "direct Project and List counts apply #{label} status selection" do
      project = project_fixture(%{})
      list = list_fixture(project)
      sibling = list_fixture(project)
      other_project = project_fixture(%{})

      for location <- [nil, list],
          {status, count} <- [
            icebox: 2,
            pending: 3,
            in_progress: 4,
            in_review: 1,
            done: 5,
            will_not_do: 6
          ],
          _ <- 1..count do
        task_fixture(project, location, %{status: status})
      end

      task_fixture(project, sibling, %{status: :in_review})
      task_fixture(other_project, %{status: :in_review})

      counts = Tasks.count_tasks_by_location(project, unquote(statuses), false)

      assert counts[nil] == unquote(Macro.escape(expected))
      assert counts[list.id] == unquote(Macro.escape(expected))
      assert map_size(counts) == 3

      for location <- [nil, list] do
        assert {:ok, tasks} =
                 Tasks.list_tasks_for_location(project, location, statuses: unquote(statuses))

        location_id = if location, do: location.id
        assert counts[location_id].visible == length(tasks)
      end
    end
  end

  test "rollup reaches every empty ancestor and counts Tasks by owning location" do
    project = project_fixture(%{})
    root = list_fixture(project)
    middle = list_fixture(project, root)
    branch = list_fixture(project, middle)
    leaf = list_fixture(project, branch)
    sibling = list_fixture(project)
    parent_task = task_fixture(project, sibling, %{status: :pending})
    task_fixture(project, leaf, %{status: :in_review}, parent: parent_task)
    task_fixture(project, leaf, %{status: :done})
    task_fixture(project, %{status: :in_review}, parent: parent_task)
    task_fixture(project_fixture(%{}), %{status: :in_review})

    direct = Tasks.count_tasks_by_location(project, [:in_review], false)
    assert direct[nil] == %{visible: 1, eligible: 1}
    assert direct[leaf.id] == %{visible: 1, eligible: 1}
    assert direct[sibling.id] == %{visible: 0, eligible: 1}

    for list <- [root, middle, branch] do
      assert direct[list.id] == %{visible: 0, eligible: 0}
    end

    rolled_up = Tasks.count_tasks_by_location(project, [:in_review], true)
    assert rolled_up[nil] == %{visible: 2, eligible: 3}
    assert rolled_up[sibling.id] == %{visible: 0, eligible: 1}

    for list <- [root, middle, branch, leaf] do
      assert rolled_up[list.id] == %{visible: 1, eligible: 1}
    end

    for {include_descendants?, counts} <- [{false, direct}, {true, rolled_up}],
        location <- [nil, root, middle, leaf, sibling] do
      assert {:ok, tasks} =
               Tasks.list_tasks_for_location(project, location,
                 statuses: [:in_review],
                 include_descendants: include_descendants?
               )

      location_id = if location, do: location.id
      assert counts[location_id].visible == length(tasks)
    end
  end

  test "from_rows rolls up unordered rows and preserves direct pairs when disabled" do
    rows = [row(2, 1, 1, 2), row(nil, nil, 3, 4), row(1, nil, 5, 6), row(3, 1, 7, 8)]

    assert Counts.from_rows(rows, false) == %{
             nil => %{visible: 3, eligible: 4},
             1 => %{visible: 5, eligible: 6},
             2 => %{visible: 1, eligible: 2},
             3 => %{visible: 7, eligible: 8}
           }

    expected = %{
      nil => %{visible: 16, eligible: 20},
      1 => %{visible: 13, eligible: 16},
      2 => %{visible: 1, eligible: 2},
      3 => %{visible: 7, eligible: 8}
    }

    assert Counts.from_rows(rows, true) == expected
    assert Counts.from_rows(Enum.reverse(rows), true) == expected
  end

  test "from_rows terminates at the Project root with no Lists" do
    assert Counts.from_rows([row(nil, nil, 2, 3)], true) ==
             %{nil => %{visible: 2, eligible: 3}}

    assert Counts.from_rows([row(nil, nil, 0, 0)], true) ==
             %{nil => %{visible: 0, eligible: 0}}
  end

  test "from_rows rolls up a thousand nested Lists" do
    rows =
      for id <- 1..1000 do
        parent_id = if id > 1, do: id - 1
        count = if id == 1000, do: 1, else: 0
        row(id, parent_id, count, count)
      end

    counts = Counts.from_rows([row(nil, nil, 0, 0) | rows], true)

    assert map_size(counts) == 1001
    assert counts[nil] == %{visible: 1, eligible: 1}

    for id <- 1..1000 do
      assert counts[id] == %{visible: 1, eligible: 1}
    end
  end

  test "counting issues one query for an empty Project and nested Lists" do
    project = project_fixture(%{})
    test_pid = self()
    handler_id = {__MODULE__, make_ref()}

    :ok =
      :telemetry.attach(
        handler_id,
        [:taskman, :repo, :query],
        fn _event, _measurements, metadata, owner ->
          if self() == owner, do: send(owner, {:count_query, metadata.query})
        end,
        test_pid
      )

    on_exit(fn -> :telemetry.detach(handler_id) end)

    assert Tasks.count_tasks_by_location(project, [], true) ==
             %{nil => %{visible: 0, eligible: 0}}

    assert_receive {:count_query, _query}
    refute_received {:count_query, _query}

    :telemetry.detach(handler_id)
    root = list_fixture(project)
    leaf = list_fixture(project, list_fixture(project, root))
    task_fixture(project, leaf, %{status: :in_review})

    :ok =
      :telemetry.attach(
        handler_id,
        [:taskman, :repo, :query],
        fn _event, _measurements, metadata, owner ->
          if self() == owner, do: send(owner, {:count_query, metadata.query})
        end,
        test_pid
      )

    counts = Tasks.count_tasks_by_location(project, [:in_review], true)
    assert counts[nil] == %{visible: 1, eligible: 1}
    assert counts[root.id] == %{visible: 1, eligible: 1}
    assert_receive {:count_query, _query}
    refute_received {:count_query, _query}
  end

  defp row(id, parent_id, visible, eligible) do
    %{
      list_id: id,
      parent_list_id: parent_id,
      visible_count: visible,
      eligible_count: eligible
    }
  end
end
