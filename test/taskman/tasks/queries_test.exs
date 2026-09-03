defmodule Taskman.Tasks.QueriesTest do
  use Taskman.DataCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.Task
  alias Taskman.Tasks.TaskWithLocation

  test "change_task/2 builds a valid Project-owned creation changeset" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})

    changeset =
      Tasks.change_task(project, %{
        title: "  Planned task  ",
        description: "Creation details",
        status: :in_progress,
        priority: :high,
        due_at: ~N[2026-08-03 16:00:00],
        project_id: other_project.id
      })

    assert changeset.valid?
    assert Ecto.Changeset.get_field(changeset, :project_id) == project.id
    assert Ecto.Changeset.get_field(changeset, :title) == "Planned task"
    assert Ecto.Changeset.get_field(changeset, :description) == "Creation details"
    assert Ecto.Changeset.get_field(changeset, :status) == :in_progress
    assert Ecto.Changeset.get_field(changeset, :priority) == :high
    assert Ecto.Changeset.get_field(changeset, :due_at) == ~N[2026-08-03 16:00:00]
  end

  test "list_tasks_for_project/1 returns only that Project's tasks in stable order" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    {:ok, first} = Tasks.create_task(project, %{title: "First"})
    {:ok, second} = Tasks.create_task(project, %{title: "Second"})
    {:ok, _other} = Tasks.create_task(other_project, %{title: "Other"})

    assert Tasks.list_tasks_for_project(project) == [first, second]
  end

  test "search_parent_candidates/4 and get_task_hierarchy/2 expose the Project-scoped query layer" do
    project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})
    root = task_fixture(project, list, %{title: "Root"})
    child = task_fixture(project, %{title: "Child"}, parent: root)
    child_id = child.id
    root_id = root.id

    assert [%TaskWithLocation{task: ^root, location_path: [^list]}] =
             Tasks.search_parent_candidates(project, child, "root")

    assert {:ok,
            %Taskman.Tasks.Hierarchy{
              selected_task_id: ^child_id,
              root: %{task: %{id: ^root_id}}
            }} = Tasks.get_task_hierarchy(project, child)

    assert child_id == child.id
    assert root_id == root.id
  end

  test "list_tasks_for_location/3 returns direct Project Tasks with empty paths" do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    direct = task_fixture(project, %{title: "Direct"})
    listed_task = task_fixture(project, root, %{title: "Listed"})

    assert {:ok, direct_listed} =
             Tasks.list_tasks_for_location(project, nil, include_descendants: false)

    assert Enum.map(direct_listed, & &1.task.id) == [direct.id]
    assert [%TaskWithLocation{task: ^direct, location_path: []}] = direct_listed

    assert {:ok, descendants} =
             Tasks.list_tasks_for_location(project, nil, include_descendants: true)

    assert Enum.map(descendants, & &1.task.id) == [direct.id, listed_task.id]
    assert Enum.at(descendants, 1).location_path == [root]
  end

  test "list_tasks_for_location/3 returns direct and descendant List Tasks with full paths" do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    child = list_fixture(project, root, %{name: "Launch"})
    leaf = list_fixture(project, child, %{name: "Copy"})
    direct = task_fixture(project, root, %{title: "Root task"})
    nested = task_fixture(project, leaf, %{title: "Nested task"})

    _unrelated =
      task_fixture(project, list_fixture(project, nil, %{name: "Other"}), %{title: "Other task"})

    assert {:ok, direct_only} =
             Tasks.list_tasks_for_location(project, root, include_descendants: false)

    assert Enum.map(direct_only, & &1.task.id) == [direct.id]
    assert Enum.map(hd(direct_only).location_path, & &1.name) == ["Planning"]

    assert {:ok, descendants} =
             Tasks.list_tasks_for_location(project, root, include_descendants: true)

    assert Enum.map(descendants, & &1.task.id) == [direct.id, nested.id]

    assert Enum.map(Enum.at(descendants, 1).location_path, & &1.name) == [
             "Planning",
             "Launch",
             "Copy"
           ]
  end

  test "list_tasks_for_location/3 keeps Project descendant results in global Task order" do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    direct = task_fixture(project, %{title: "Direct"})
    listed = task_fixture(project, root, %{title: "Listed"})

    timestamp = ~U[2026-08-03 16:00:00.000000Z]
    Repo.update_all(Task, set: [inserted_at: timestamp])

    assert {:ok, descendants} =
             Tasks.list_tasks_for_location(project, nil, include_descendants: true)

    assert Enum.map(descendants, & &1.task.id) == Enum.sort([direct.id, listed.id])
  end

  test "list_tasks_for_location/3 excludes Tasks outside the selected statuses" do
    project = project_fixture(%{})
    pending = task_fixture(project, %{title: "Pending", status: :pending})
    done = task_fixture(project, %{title: "Done", status: :done})
    _will_not_do = task_fixture(project, %{title: "Rejected", status: :will_not_do})

    assert {:ok, tasks} =
             Tasks.list_tasks_for_location(project, nil, statuses: [:pending, :done])

    assert Enum.map(tasks, & &1.task.id) == [pending.id, done.id]
  end

  test "list_tasks_for_location/3 preserves exact status membership semantics" do
    project = project_fixture(%{})
    pending = task_fixture(project, %{title: "Pending", status: :pending})

    assert {:ok, [%TaskWithLocation{task: ^pending}]} =
             Tasks.list_tasks_for_location(project, nil, statuses: [:pending])

    assert {:ok, []} =
             Tasks.list_tasks_for_location(project, nil, statuses: ["pending"])

    assert {:ok, []} =
             Tasks.list_tasks_for_location(project, nil, statuses: [:bogus])

    assert {:ok, [%TaskWithLocation{task: ^pending}]} =
             Tasks.list_tasks_for_location(project, nil, statuses: [:pending, :bogus])
  end

  test "list_tasks_for_location/3 returns no Tasks for empty statuses" do
    project = project_fixture(%{})
    _task = task_fixture(project, %{title: "Pending", status: :pending})
    test_pid = self()
    handler_id = {__MODULE__, make_ref()}

    :ok =
      :telemetry.attach(
        handler_id,
        [:taskman, :repo, :query],
        fn _event, _measurements, %{query: query}, ^test_pid ->
          if is_binary(query) and String.contains?(query, ~s(FROM "tasks")) do
            send(test_pid, {:task_query, query})
          end
        end,
        test_pid
      )

    on_exit(fn -> :telemetry.detach(handler_id) end)

    assert {:ok, []} = Tasks.list_tasks_for_location(project, nil, statuses: [])
    assert_receive {:task_query, query}
    assert query =~ "FALSE"
  end

  test "list_tasks_for_location/3 pushes status filters and field sorting into SQL for every location mode" do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    child = list_fixture(project, root, %{name: "Launch"})
    _direct = task_fixture(project, %{title: "Direct", status: :pending})
    _root_task = task_fixture(project, root, %{title: "Root", status: :pending})
    _child_task = task_fixture(project, child, %{title: "Child", status: :pending})
    _done = task_fixture(project, %{title: "Done", status: :done})
    test_pid = self()
    handler_id = {__MODULE__, make_ref()}

    :ok =
      :telemetry.attach(
        handler_id,
        [:taskman, :repo, :query],
        fn _event, _measurements, %{query: query}, ^test_pid ->
          if is_binary(query) and String.contains?(query, ~s(FROM "tasks")) do
            send(test_pid, {:task_query, query})
          end
        end,
        test_pid
      )

    on_exit(fn -> :telemetry.detach(handler_id) end)

    locations = [
      {nil, [include_descendants: false]},
      {nil, [include_descendants: true]},
      {root, [include_descendants: false]},
      {root, [include_descendants: true]}
    ]

    for {location, location_opts} <- locations do
      assert {:ok, _tasks} =
               Tasks.list_tasks_for_location(
                 project,
                 location,
                 Keyword.merge(location_opts,
                   statuses: [:pending],
                   sort: {:title, :desc}
                 )
               )

      assert_receive {:task_query, query}
      assert query =~ ~s(t0."status" = ANY)
      assert query =~ ~s|ORDER BY lower(t0."title") DESC, t0."id" DESC|
    end
  end

  test "list_tasks_for_location/3 uses direction-aware Task ID tie-breaks for stored fields" do
    project = project_fixture(%{})

    first =
      task_fixture(project, %{
        title: "Same title",
        status: :pending,
        priority: :high
      })

    second =
      task_fixture(project, %{
        title: "same title",
        status: :pending,
        priority: :high
      })

    for field <- [:title, :status, :priority] do
      assert {:ok, ascending} =
               Tasks.list_tasks_for_location(project, nil, sort: {field, :asc})

      assert Enum.map(ascending, & &1.task.id) == [first.id, second.id]

      assert {:ok, descending} =
               Tasks.list_tasks_for_location(project, nil, sort: {field, :desc})

      assert Enum.map(descending, & &1.task.id) == [second.id, first.id]
    end
  end

  test "list_tasks_for_location/3 sorts Task fields in either direction" do
    project = project_fixture(%{})

    alpha =
      task_fixture(project, %{
        title: "Alpha",
        status: :pending,
        priority: :urgent
      })

    omega =
      task_fixture(project, %{
        title: "omega",
        status: :done,
        priority: :low
      })

    assert {:ok, by_id} =
             Tasks.list_tasks_for_location(project, nil, sort: {:id, :desc})

    assert Enum.map(by_id, & &1.task.id) == [omega.id, alpha.id]

    assert {:ok, by_title} =
             Tasks.list_tasks_for_location(project, nil, sort: {:title, :asc})

    assert Enum.map(by_title, & &1.task.id) == [alpha.id, omega.id]

    assert {:ok, by_status} =
             Tasks.list_tasks_for_location(project, nil, sort: {:status, :desc})

    assert Enum.map(by_status, & &1.task.id) == [omega.id, alpha.id]

    assert {:ok, by_priority} =
             Tasks.list_tasks_for_location(project, nil, sort: {:priority, :desc})

    assert Enum.map(by_priority, & &1.task.id) == [alpha.id, omega.id]
  end

  test "list_tasks_for_location/3 sorts descendant Tasks by their full location path" do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    delivery = list_fixture(project, nil, %{name: "delivery"})
    planning_task = task_fixture(project, planning, %{title: "Planning task"})
    delivery_task = task_fixture(project, delivery, %{title: "Delivery task"})

    assert {:ok, tasks} =
             Tasks.list_tasks_for_location(project, nil,
               include_descendants: true,
               sort: {:location, :asc}
             )

    assert Enum.map(tasks, & &1.task.id) == [delivery_task.id, planning_task.id]
  end

  test "list_tasks_for_location/3 rejects a foreign List before listing" do
    project = project_fixture(%{})
    foreign_list = list_fixture(project_fixture(%{}), nil, %{name: "Foreign"})

    assert {:error, :not_found} =
             Tasks.list_tasks_for_location(project, foreign_list, include_descendants: true)
  end
end
