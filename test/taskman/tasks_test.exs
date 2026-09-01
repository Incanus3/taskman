defmodule Taskman.TasksTest do
  use Taskman.DataCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.Task
  alias Taskman.Tasks.TaskWithLocation
  alias Taskman.ChangeNotifications
  alias Taskman.ChangeNotifications.Event

  test "create_task/2 assigns ownership and product defaults" do
    project = project_fixture(%{})

    assert {:ok, task} = Tasks.create_task(project, %{title: "First task"})
    assert task.project_id == project.id
    assert task.title == "First task"
    assert task.description == ""
    assert task.status == :pending
    assert task.priority == :none
  end

  test "create_task/4 creates a Task with a same-Project parent" do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})

    assert {:ok, child} =
             Tasks.create_task(project, nil, %{title: "Child"}, parent: parent)

    assert child.parent_task_id == parent.id
  end

  test "create_task/3 creates a direct Project Task and ignores a supplied list ID" do
    project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})

    assert {:ok, task} =
             Tasks.create_task(project, nil, %{
               title: "Direct task",
               list_id: list.id
             })

    assert task.project_id == project.id
    assert task.list_id == nil
  end

  test "create_task/3 creates a List-owned Task and ignores supplied ownership IDs" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})
    other_list = list_fixture(other_project, nil, %{name: "Other"})

    assert {:ok, task} =
             Tasks.create_task(project, list, %{
               title: "List task",
               project_id: other_project.id,
               list_id: other_list.id
             })

    assert task.project_id == project.id
    assert task.list_id == list.id
  end

  test "create_task/3 rejects a List owned by another Project" do
    project = project_fixture(%{})
    foreign_list = list_fixture(project_fixture(%{}), nil, %{name: "Foreign"})

    assert {:error, :not_found} = Tasks.create_task(project, foreign_list, %{title: "No leak"})
  end

  test "create_task/2 persists an explicitly empty description as an empty string" do
    project = project_fixture(%{})

    assert {:ok, task} =
             Tasks.create_task(project, %{title: "Empty description", description: ""})

    assert task.description == ""
  end

  test "the database defaults an omitted Task description to an empty string" do
    project = project_fixture(%{})

    result =
      Ecto.Adapters.SQL.query!(
        Repo,
        """
        INSERT INTO tasks (project_id, title, inserted_at, updated_at)
        VALUES ($1, $2, NOW(), NOW())
        RETURNING id, description
        """,
        [project.id, "Database default"]
      )

    assert [[task_id, ""]] = result.rows

    assert %{rows: [[""]]} =
             Ecto.Adapters.SQL.query!(
               Repo,
               "SELECT description FROM tasks WHERE id = $1",
               [task_id]
             )
  end

  test "the database rejects an explicitly null Task description without aborting the sandbox" do
    project = project_fixture(%{})

    error =
      assert_raise Postgrex.Error, fn ->
        Repo.transaction(
          fn ->
            Ecto.Adapters.SQL.query!(
              Repo,
              """
              INSERT INTO tasks (project_id, title, description, inserted_at, updated_at)
              VALUES ($1, $2, $3, NOW(), NOW())
              """,
              [project.id, "Rejected null", nil]
            )
          end,
          mode: :savepoint
        )
      end

    assert error.postgres.code == :not_null_violation

    assert %{rows: [[1]]} = Ecto.Adapters.SQL.query!(Repo, "SELECT 1")
  end

  test "create_task/2 requires a title and ignores user-owned project IDs" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})

    assert {:error, changeset} = Tasks.create_task(project, %{title: ""})
    assert %{title: [_]} = errors_on(changeset)

    assert {:ok, task} =
             Tasks.create_task(project, %{title: "Owned safely", project_id: other_project.id})

    assert task.project_id == project.id
  end

  test "ordinary create and update attrs cannot change a Task's list ownership" do
    project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Owned"})

    assert {:ok, updated} =
             Tasks.update_task(project, task, %{title: "Still owned", list_id: list.id})

    assert updated.list_id == nil

    assert Ecto.Changeset.get_field(
             Tasks.change_task(project, %{title: "New", list_id: list.id}),
             :list_id
           ) == nil
  end

  test "update_task/3 leaves parentage unchanged" do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})
    child = task_fixture(project, %{title: "Child"}, parent: parent)

    assert {:ok, updated} = Tasks.update_task(project, child, %{title: "Renamed child"})
    assert updated.parent_task_id == parent.id
  end

  test "update_task/4 replaces, clears, and idempotently sets a parent" do
    project = project_fixture(%{})
    first_parent = task_fixture(project, %{title: "First parent"})
    second_parent = task_fixture(project, %{title: "Second parent"})
    child = task_fixture(project, %{title: "Child"})

    assert {:ok, child} = Tasks.update_task(project, child, %{}, parent: first_parent)
    assert child.parent_task_id == first_parent.id

    assert {:ok, child} = Tasks.update_task(project, child, %{}, parent: first_parent)
    assert child.parent_task_id == first_parent.id

    assert {:ok, child} = Tasks.update_task(project, child, %{}, parent: second_parent)
    assert child.parent_task_id == second_parent.id

    assert {:ok, child} = Tasks.update_task(project, child, %{}, parent: nil)
    assert child.parent_task_id == nil

    assert {:ok, child} = Tasks.update_task(project, child, %{}, parent: nil)
    assert child.parent_task_id == nil
  end

  test "parent mutations reject foreign parents without leaking their identity" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    child = task_fixture(project, %{title: "Child"})
    foreign_parent = task_fixture(other_project, %{title: "Foreign parent"})

    assert {:error, :not_found} =
             Tasks.create_task(project, nil, %{title: "Rejected child"}, parent: foreign_parent)

    assert {:error, :not_found} =
             Tasks.update_task(project, child, %{title: "Rejected rename"},
               parent: foreign_parent
             )

    persisted_child = Tasks.get_task_for_project(project, child.id)
    assert persisted_child.title == "Child"
    assert persisted_child.parent_task_id == nil
  end

  test "parent mutations reject stale Tasks and parents" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Task"})
    parent = task_fixture(project, %{title: "Parent"})

    Repo.delete!(parent)

    assert {:error, :not_found} =
             Tasks.update_task(project, task, %{title: "Rejected rename"}, parent: parent)

    assert Tasks.get_task_for_project(project, task.id).title == "Task"

    Repo.delete!(task)

    assert {:error, :not_found} = Tasks.update_task(project, task, %{}, parent: nil)
  end

  test "parentage changes do not change List ownership" do
    project = project_fixture(%{})
    parent_list = list_fixture(project, nil, %{name: "Parent location"})
    child_list = list_fixture(project, nil, %{name: "Child location"})
    parent = task_fixture(project, parent_list, %{title: "Parent"})
    child = task_fixture(project, child_list, %{title: "Child"})

    assert {:ok, updated} = Tasks.update_task(project, child, %{}, parent: parent)
    assert updated.parent_task_id == parent.id
    assert updated.list_id == child_list.id
  end

  test "update_task/4 atomically persists an ordinary field with a parent change" do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})
    child = task_fixture(project, %{title: "Before"})

    assert {:ok, updated} =
             Tasks.update_task(project, child, %{title: "After"}, parent: parent)

    assert updated.title == "After"
    assert updated.parent_task_id == parent.id
  end

  test "update_task/4 atomically rejects ordinary changes with an invalid parent" do
    project = project_fixture(%{})
    child = task_fixture(project, %{title: "Before"})

    assert {:error, changeset} =
             Tasks.update_task(project, child, %{title: "After"}, parent: child)

    assert "would create a cycle" in errors_on(changeset).parent_task_id

    persisted_child = Tasks.get_task_for_project(project, child.id)
    assert persisted_child.title == "Before"
    assert persisted_child.parent_task_id == nil
  end

  test "update_task/4 rejects direct self-parenting" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Self"})

    assert {:error, changeset} = Tasks.update_task(project, task, %{}, parent: task)
    assert "would create a cycle" in errors_on(changeset).parent_task_id
    assert Tasks.get_task_for_project(project, task.id).parent_task_id == nil
  end

  test "update_task/4 rejects a parent that would close a deep cycle" do
    project = project_fixture(%{})
    root = task_fixture(project, %{title: "Root"})
    middle = task_fixture(project, %{title: "Middle"}, parent: root)
    leaf = task_fixture(project, %{title: "Leaf"}, parent: middle)

    assert {:error, changeset} = Tasks.update_task(project, root, %{}, parent: leaf)
    assert "would create a cycle" in errors_on(changeset).parent_task_id

    assert Tasks.get_task_for_project(project, root.id).parent_task_id == nil
  end

  test "concurrent opposite reparent attempts cannot create a cycle" do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      project = project_fixture(%{})
      first = task_fixture(project, %{title: "First"})
      second = task_fixture(project, %{title: "Second"})
      function_name = "taskman_parent_mutation_gate_#{System.unique_integer([:positive])}"
      trigger_name = "#{function_name}_trigger"
      gate_key = System.unique_integer([:positive])

      on_exit(fn ->
        Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
          Ecto.Adapters.SQL.query!(Repo, "DROP TRIGGER IF EXISTS #{trigger_name} ON tasks")
          Ecto.Adapters.SQL.query!(Repo, "DROP FUNCTION IF EXISTS #{function_name}()")

          case Repo.get(Taskman.Projects.Project, project.id) do
            nil -> :ok
            persisted_project -> Repo.delete!(persisted_project)
          end
        end)
      end)

      Ecto.Adapters.SQL.query!(
        Repo,
        """
        CREATE FUNCTION #{function_name}()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF NEW.id = TG_ARGV[0]::bigint THEN
            PERFORM pg_advisory_xact_lock(TG_ARGV[1]::bigint);
          END IF;

          RETURN NEW;
        END;
        $$
        """
      )

      Ecto.Adapters.SQL.query!(
        Repo,
        """
        CREATE TRIGGER #{trigger_name}
        BEFORE UPDATE OF parent_task_id ON tasks
        FOR EACH ROW
        EXECUTE FUNCTION #{function_name}(#{first.id}, #{gate_key})
        """
      )

      workers =
        with_parent_update_gate(gate_key, fn ->
          gate_owner_backend = database_backend_pid()

          supervisor = start_supervised!(Elixir.Task.Supervisor)
          test_pid = self()
          first_worker = start_parent_update_worker(supervisor, project, first, second, test_pid)
          second_worker = start_parent_update_worker(supervisor, project, second, first, test_pid)
          first_pid = first_worker.pid
          second_pid = second_worker.pid
          backend_pids = await_parent_update_readiness([first_worker, second_worker])
          first_backend = Map.fetch!(backend_pids, first_pid)
          second_backend = Map.fetch!(backend_pids, second_pid)

          send(first_pid, :start_parent_update)
          await_parent_update_block(first_backend, gate_owner_backend)

          send(second_pid, :start_parent_update)
          await_parent_update_block(second_backend, first_backend)

          {first_worker, second_worker}
        end)

      results = await_parent_update_results(Tuple.to_list(workers))

      assert Enum.count(results, &match?({:ok, _task}, &1)) == 1

      assert Enum.any?(results, fn result ->
               match?({:error, %Ecto.Changeset{}}, result)
             end)

      persisted_first = Repo.get!(Task, first.id)
      persisted_second = Repo.get!(Task, second.id)

      refute persisted_first.parent_task_id == second.id and
               persisted_second.parent_task_id == first.id
    end)
  end

  test "hierarchy projection holds a shared Project lock until its tree is complete" do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      project = project_fixture(%{})
      old_root = task_fixture(project, %{title: "Old root"})
      selected = task_fixture(project, %{title: "Selected"}, parent: old_root)
      test_pid = self()
      handler_id = {__MODULE__, make_ref()}
      query_gate = make_ref()
      telemetry_guard = :atomics.new(1, [])

      on_exit(fn ->
        :telemetry.detach(handler_id)

        Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
          case Repo.get(Taskman.Projects.Project, project.id) do
            nil -> :ok
            persisted_project -> Repo.delete!(persisted_project)
          end
        end)
      end)

      :ok =
        :telemetry.attach(
          handler_id,
          [:taskman, :repo, :query],
          fn _event, _measurements, %{query: query}, _config ->
            if String.contains?(query, ~s("task_parent_ancestors")) and
                 :atomics.compare_exchange(telemetry_guard, 1, 0, 1) == :ok do
              send(test_pid, {:hierarchy_root_discovered, self()})

              receive do
                {:continue_hierarchy, ^query_gate} -> :ok
              end
            end
          end,
          nil
        )

      supervisor = start_supervised!(Elixir.Task.Supervisor)

      {:ok, hierarchy_pid} =
        Elixir.Task.Supervisor.start_child(supervisor, fn ->
          :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo, sandbox: false)

          try do
            send(test_pid, {:hierarchy_ready, self()})

            receive do
              :load_hierarchy ->
                result = Tasks.get_task_hierarchy(project, selected)
                send(test_pid, {:hierarchy_finished, self(), result})
            end
          after
            :ok = Ecto.Adapters.SQL.Sandbox.checkin(Repo)
          end
        end)

      hierarchy_ref = Process.monitor(hierarchy_pid)
      assert_receive {:hierarchy_ready, ^hierarchy_pid}, 5_000
      send(hierarchy_pid, :load_hierarchy)
      assert_receive {:hierarchy_root_discovered, ^hierarchy_pid}, 5_000

      error =
        assert_raise Postgrex.Error, fn ->
          Ecto.Adapters.SQL.query!(
            Repo,
            "SELECT id FROM projects WHERE id = $1 FOR UPDATE NOWAIT",
            [project.id]
          )
        end

      assert error.postgres.code == :lock_not_available

      send(hierarchy_pid, {:continue_hierarchy, query_gate})
      old_root_id = old_root.id
      selected_id = selected.id

      assert_receive {:hierarchy_finished, ^hierarchy_pid,
                      {:ok,
                       %{
                         selected_task_id: ^selected_id,
                         root: %{
                           task: %{id: ^old_root_id},
                           children: [%{task: %{id: ^selected_id}}]
                         }
                       }}},
                     5_000

      assert_receive {:DOWN, ^hierarchy_ref, :process, ^hierarchy_pid, :normal}, 5_000
    end)
  end

  test "parent update gate releases its session lock after a failure" do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      gate_key = System.unique_integer([:positive])

      try do
        assert_raise RuntimeError, "forced parent update failure", fn ->
          with_parent_update_gate(gate_key, fn -> raise "forced parent update failure" end)
        end

        assert parent_update_gate_available?(gate_key)
      after
        Ecto.Adapters.SQL.query!(Repo, "SELECT pg_advisory_unlock($1)", [gate_key])
      end
    end)
  end

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

  test "move_task/3 moves a Task among same-Project locations and detects no-op moves" do
    project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me"})

    assert {:ok, moved} = Tasks.move_task(project, task, destination)
    assert moved.list_id == destination.id

    assert {:error, :unchanged_location} = Tasks.move_task(project, moved, destination)

    assert {:ok, moved_direct} = Tasks.move_task(project, moved, nil)
    assert moved_direct.list_id == nil
  end

  test "move_task/3 rejects foreign or stale Tasks and destinations" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Planning"})
    foreign_destination = list_fixture(other_project, nil, %{name: "Foreign"})
    task = task_fixture(project, %{title: "Move me"})
    foreign_task = task_fixture(other_project, %{title: "Foreign task"})

    assert {:error, :not_found} = Tasks.move_task(project, task, foreign_destination)
    assert {:error, :not_found} = Tasks.move_task(project, foreign_task, destination)

    Repo.delete!(destination)
    assert {:error, :not_found} = Tasks.move_task(project, task, destination)

    Repo.delete!(task)
    assert {:error, :not_found} = Tasks.move_task(project, task, nil)
  end

  test "get_task_for_project/2 returns only a Task owned by the Project" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    task = task_fixture(project, %{title: "Owned"})
    other_task = task_fixture(other_project, %{title: "Other"})

    assert Tasks.get_task_for_project(project, task.id) == task
    assert Tasks.get_task_for_project(project, Integer.to_string(task.id)) == task
    assert Tasks.get_task_for_project(project, other_task.id) == nil
    assert Tasks.get_task_for_project(project, "not-an-id") == nil
    assert Tasks.get_task_for_project(project, -1) == nil
    assert Tasks.get_task_for_project(project, 999_999_999) == nil
  end

  test "update_task/3 persists every editable field" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    due_at = ~N[2026-08-03 16:00:00]

    attrs = %{
      title: "  After  ",
      description: "Updated",
      status: :in_review,
      priority: :urgent,
      due_at: due_at
    }

    assert {:ok, updated} = Tasks.update_task(project, task, attrs)
    assert updated.title == "After"
    assert updated.description == "Updated"
    assert updated.status == :in_review
    assert updated.priority == :urgent
    assert updated.due_at == due_at
  end

  test "update_task/3 rejects a blank title without changing the persisted Task" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Original title"})

    assert {:error, changeset} = Tasks.update_task(project, task, %{title: ""})
    assert %{title: [_]} = errors_on(changeset)
    assert Tasks.get_task_for_project(project, task.id).title == "Original title"
  end

  test "update_task/3 keeps ownership immutable and rejects a mismatched Project" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    task = task_fixture(project, %{title: "Owned"})

    assert {:ok, updated} =
             Tasks.update_task(project, task, %{
               title: "Still owned",
               project_id: other_project.id
             })

    assert updated.project_id == project.id
    assert {:error, :not_found} = Tasks.update_task(other_project, task, %{title: "Leaked"})
    assert Tasks.get_task_for_project(project, task.id).title == "Still owned"
  end

  test "update_task/3 accepts every fixed lifecycle status" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Lifecycle"})

    Enum.reduce(Task.statuses(), task, fn status, current ->
      assert {:ok, updated} = Tasks.update_task(project, current, %{status: status})
      assert updated.status == status
      updated
    end)
  end

  test "update_task/3 accepts every fixed priority" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Priority"})

    Enum.reduce(Task.priorities(), task, fn priority, current ->
      assert {:ok, updated} = Tasks.update_task(project, current, %{priority: priority})
      assert updated.priority == priority
      updated
    end)
  end

  test "stale ordinary updates retry disjoint intended fields" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", status: :pending})
    {first_baseline, second_baseline} = loaded_task_baselines(project, task)

    assert {:ok, titled} = Tasks.update_task(project, first_baseline, %{title: "Changed"})
    assert {:ok, merged} = Tasks.update_task(project, second_baseline, %{status: :done})

    assert merged.title == "Changed"
    assert merged.status == :done
    assert merged.lock_version == titled.lock_version + 1
  end

  test "stale same-field updates return a conflict without data loss" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    {first_baseline, second_baseline} = loaded_task_baselines(project, task)

    assert {:ok, persisted} =
             Tasks.update_task(project, first_baseline, %{title: "First writer"})

    assert {:error, %{__struct__: Taskman.Tasks.Conflict, task: current, fields: [:title]}} =
             Tasks.update_task(project, second_baseline, %{title: "Second writer"})

    assert current == persisted
    assert Tasks.get_task_for_project(project, task.id).title == "First writer"
  end

  test "a stale request already satisfied by persistence returns the current Task without a write" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    {first_baseline, second_baseline} = loaded_task_baselines(project, task)

    assert {:ok, persisted} =
             Tasks.update_task(project, first_baseline, %{title: "Changed"})

    assert {:ok, current} = Tasks.update_task(project, second_baseline, %{title: "Changed"})
    assert current == persisted
    assert current.lock_version == persisted.lock_version
  end

  test "a stale mixed-field update fails atomically when one intended field conflicts" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", status: :pending})
    {first_baseline, second_baseline} = loaded_task_baselines(project, task)

    assert {:ok, _persisted} =
             Tasks.update_task(project, first_baseline, %{title: "First writer"})

    assert {:error, %{__struct__: Taskman.Tasks.Conflict, fields: [:title]}} =
             Tasks.update_task(project, second_baseline, %{
               title: "Second writer",
               status: :done
             })

    current = Tasks.get_task_for_project(project, task.id)
    assert current.title == "First writer"
    assert current.status == :pending
  end

  test "stale parent changes retry disjoint ordinary fields after revalidating hierarchy" do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})
    child = task_fixture(project, %{title: "Child", status: :pending})
    {first_baseline, second_baseline} = loaded_task_baselines(project, child)

    assert {:ok, titled} = Tasks.update_task(project, first_baseline, %{title: "Changed"})

    assert {:ok, merged} =
             Tasks.update_task(project, second_baseline, %{status: :done}, parent: parent)

    assert merged.title == "Changed"
    assert merged.status == :done
    assert merged.parent_task_id == parent.id
    assert merged.lock_version == titled.lock_version + 1
  end

  test "stale parent changes conflict on parent_task_id" do
    project = project_fixture(%{})
    first_parent = task_fixture(project, %{title: "First parent"})
    second_parent = task_fixture(project, %{title: "Second parent"})
    child = task_fixture(project, %{title: "Child"})
    {first_baseline, second_baseline} = loaded_task_baselines(project, child)

    assert {:ok, _persisted} =
             Tasks.update_task(project, first_baseline, %{}, parent: first_parent)

    assert {:error, %{__struct__: Taskman.Tasks.Conflict, fields: [:parent_task_id]}} =
             Tasks.update_task(project, second_baseline, %{}, parent: second_parent)

    assert Tasks.get_task_for_project(project, child.id).parent_task_id == first_parent.id
  end

  test "a stale parent request equal to its baseline still conflicts when parentage changed" do
    project = project_fixture(%{})
    original_parent = task_fixture(project, %{title: "Original parent"})
    concurrent_parent = task_fixture(project, %{title: "Concurrent parent"})
    child = task_fixture(project, %{title: "Child"}, parent: original_parent)
    {first_baseline, second_baseline} = loaded_task_baselines(project, child)

    assert {:ok, persisted} =
             Tasks.update_task(project, first_baseline, %{}, parent: concurrent_parent)

    assert {:error,
            %{__struct__: Taskman.Tasks.Conflict, task: current, fields: [:parent_task_id]}} =
             Tasks.update_task(project, second_baseline, %{}, parent: original_parent)

    assert current == persisted
    assert current.parent_task_id == concurrent_parent.id
  end

  test "invalid updates retain Repo update changeset metadata" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})

    assert {:error, changeset} = Tasks.update_task(project, task, %{title: ""})
    assert changeset.action == :update
    assert changeset.repo == Repo
    assert changeset.repo_opts == []
  end

  test "parent updates reject malformed parent values as not found" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Child"})

    assert {:error, :not_found} = Tasks.update_task(project, task, %{}, parent: :not_a_task)
  end

  test "stale moves retry when the concurrent write is disjoint from list_id" do
    project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Before"})
    {first_baseline, second_baseline} = loaded_task_baselines(project, task)

    assert {:ok, titled} = Tasks.update_task(project, first_baseline, %{title: "Changed"})
    assert {:ok, moved} = Tasks.move_task(project, second_baseline, destination)

    assert moved.title == "Changed"
    assert moved.list_id == destination.id
    assert moved.lock_version == titled.lock_version + 1
  end

  test "stale moves conflict on list_id and retain unchanged_location" do
    project = project_fixture(%{})
    first_destination = list_fixture(project, nil, %{name: "Planning"})
    second_destination = list_fixture(project, nil, %{name: "Delivery"})
    task = task_fixture(project, %{title: "Before"})
    {first_baseline, second_baseline} = loaded_task_baselines(project, task)

    assert {:ok, _moved} = Tasks.move_task(project, first_baseline, first_destination)

    assert {:error, %{__struct__: Taskman.Tasks.Conflict, fields: [:list_id]}} =
             Tasks.move_task(project, second_baseline, second_destination)

    assert {:error, :unchanged_location} =
             Tasks.move_task(project, second_baseline, first_destination)

    assert Tasks.get_task_for_project(project, task.id).list_id == first_destination.id
  end

  test "a second stale race stops after one retry" do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      project = project_fixture(%{})
      task = task_fixture(project, %{title: "Before", status: :pending})
      {first_baseline, second_baseline} = loaded_task_baselines(project, task)

      on_exit(fn ->
        Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
          case Repo.get(Taskman.Projects.Project, project.id) do
            nil -> :ok
            persisted_project -> Repo.delete!(persisted_project)
          end
        end)
      end)

      assert {:ok, _persisted} =
               Tasks.update_task(project, first_baseline, %{title: "First writer"})

      test_pid = self()
      handler_id = {__MODULE__, make_ref()}
      trigger = make_ref()
      telemetry_guard = :atomics.new(1, [])
      supervisor = start_supervised!(Elixir.Task.Supervisor)

      {:ok, race_pid} =
        Elixir.Task.Supervisor.start_child(supervisor, fn ->
          :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo, sandbox: false)

          try do
            send(test_pid, {:second_race_ready, self()})

            receive do
              {:run_second_race, ^trigger} ->
                Ecto.Adapters.SQL.query!(
                  Repo,
                  """
                  UPDATE tasks
                  SET title = $2, lock_version = lock_version + 1, updated_at = NOW()
                  WHERE id = $1
                  """,
                  [task.id, "Second writer"]
                )

                send(test_pid, {:second_race_finished, self()})
            end
          after
            :ok = Ecto.Adapters.SQL.Sandbox.checkin(Repo)
          end
        end)

      race_ref = Process.monitor(race_pid)
      assert_receive {:second_race_ready, ^race_pid}, 5_000

      :ok =
        :telemetry.attach(
          handler_id,
          [:taskman, :repo, :query],
          fn _event, _measurements, %{query: query}, _config ->
            if self() == test_pid and
                 String.contains?(query, ~s(FROM "tasks")) and
                 String.starts_with?(String.trim_leading(query), "SELECT") and
                 :atomics.compare_exchange(telemetry_guard, 1, 0, 1) == :ok do
              send(race_pid, {:run_second_race, trigger})
              assert_receive {:second_race_finished, ^race_pid}, 5_000
            end
          end,
          nil
        )

      on_exit(fn -> :telemetry.detach(handler_id) end)

      assert {:error, %{__struct__: Taskman.Tasks.Conflict, fields: [:status], task: current}} =
               Tasks.update_task(project, second_baseline, %{status: :done})

      assert current.title == "Second writer"
      assert current.status == :pending
      assert_receive {:DOWN, ^race_ref, :process, ^race_pid, :normal}, 5_000
    end)
  end

  test "Task creation publishes its full persisted mutation metadata after success" do
    project = project_fixture(%{})
    topic = subscribe_task_events(project)

    assert {:ok, task} = Tasks.create_task(project, %{title: "Published"})
    refute_receive %Event{}, 50

    assert_receive {:task_event, ^topic,
                    %Event{
                      entity: :task,
                      operation: :created,
                      project_id: project_id,
                      entity_id: task_id,
                      lock_version: lock_version,
                      fields: [
                        :description,
                        :due_at,
                        :list_id,
                        :parent_task_id,
                        :priority,
                        :project_id,
                        :status,
                        :title
                      ]
                    }}

    assert project_id == project.id
    assert task_id == task.id
    assert lock_version == task.lock_version
  end

  test "ordinary Task updates publish their changed fields after persistence" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", status: :pending})
    topic = subscribe_task_events(project)

    assert {:ok, updated} =
             Tasks.update_task(project, task, %{description: "Changed", status: :done})

    assert_receive {:task_event, ^topic,
                    %Event{
                      entity: :task,
                      operation: :updated,
                      entity_id: task_id,
                      lock_version: lock_version,
                      fields: [:description, :status]
                    }}

    assert task_id == updated.id
    assert lock_version == updated.lock_version
  end

  test "parent mutations publish parent_task_id with their ordinary changed fields" do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})
    child = task_fixture(project, %{title: "Before"})
    topic = subscribe_task_events(project)

    assert {:ok, updated} =
             Tasks.update_task(project, child, %{title: "After"}, parent: parent)

    assert_receive {:task_event, ^topic,
                    %Event{
                      operation: :updated,
                      entity_id: task_id,
                      lock_version: lock_version,
                      fields: [:parent_task_id, :title]
                    }}

    assert task_id == updated.id
    assert lock_version == updated.lock_version
  end

  test "Task movement publishes only list_id after persistence" do
    project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me"})
    topic = subscribe_task_events(project)

    assert {:ok, moved} = Tasks.move_task(project, task, destination)

    assert_receive {:task_event, ^topic,
                    %Event{
                      operation: :moved,
                      entity_id: task_id,
                      lock_version: lock_version,
                      fields: [:list_id]
                    }}

    assert task_id == moved.id
    assert lock_version == moved.lock_version
  end

  test "failed, conflicting, and unchanged Task mutations publish no event" do
    project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Before"})
    topic = subscribe_task_events(project)

    assert {:error, _changeset} = Tasks.update_task(project, task, %{title: ""})
    refute_receive {:task_event, ^topic, %Event{}}, 50

    assert {:error, _changeset} = Tasks.update_task(project, task, %{}, parent: task)
    refute_receive {:task_event, ^topic, %Event{}}, 50

    {first_baseline, second_baseline} = loaded_task_baselines(project, task)
    assert {:ok, updated} = Tasks.update_task(project, first_baseline, %{title: "First writer"})
    assert_receive {:task_event, ^topic, %Event{entity_id: updated_id}}
    assert updated_id == updated.id

    assert {:error, %{__struct__: Taskman.Tasks.Conflict}} =
             Tasks.update_task(project, second_baseline, %{title: "Second writer"})

    refute_receive {:task_event, ^topic, %Event{}}, 50

    assert {:ok, moved} = Tasks.move_task(project, updated, destination)
    assert_receive {:task_event, ^topic, %Event{operation: :moved}}
    assert {:error, :unchanged_location} = Tasks.move_task(project, moved, destination)
    refute_receive {:task_event, ^topic, %Event{}}, 50
  end

  defp subscribe_task_events(project) do
    topic = "projects:#{project.id}:tasks"
    assert :ok = ChangeNotifications.subscribe_project(project)
    start_task_event_forwarder(topic)
    topic
  end

  defp start_task_event_forwarder(topic) do
    test_pid = self()

    start_supervised!(
      {Elixir.Task,
       fn ->
         :ok = Phoenix.PubSub.subscribe(Taskman.PubSub, topic)
         send(test_pid, {:task_event_forwarder_ready, topic})
         forward_task_events(test_pid, topic)
       end},
      id: {:task_event_forwarder, topic}
    )

    assert_receive {:task_event_forwarder_ready, ^topic}
  end

  defp forward_task_events(test_pid, topic) do
    receive do
      event ->
        send(test_pid, {:task_event, topic, event})
        forward_task_events(test_pid, topic)
    end
  end

  defp start_parent_update_worker(supervisor, project, task, parent, test_pid) do
    {:ok, pid} =
      Elixir.Task.Supervisor.start_child(supervisor, fn ->
        :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo, sandbox: false)

        try do
          send(test_pid, {:parent_update_ready, self(), database_backend_pid()})

          receive do
            :start_parent_update ->
              result = Tasks.update_task(project, task, %{}, parent: parent)
              send(test_pid, {:parent_update_finished, self(), result})
          end
        after
          :ok = Ecto.Adapters.SQL.Sandbox.checkin(Repo)
        end
      end)

    %{pid: pid, ref: Process.monitor(pid)}
  end

  defp database_backend_pid do
    assert %{rows: [[backend_pid]]} = Ecto.Adapters.SQL.query!(Repo, "SELECT pg_backend_pid()")
    backend_pid
  end

  defp with_parent_update_gate(gate_key, fun) do
    Ecto.Adapters.SQL.query!(Repo, "SELECT pg_advisory_lock($1)", [gate_key])

    try do
      fun.()
    after
      assert %{rows: [[true]]} =
               Ecto.Adapters.SQL.query!(Repo, "SELECT pg_advisory_unlock($1)", [gate_key])
    end
  end

  defp parent_update_gate_available?(gate_key) do
    supervisor = start_supervised!(Elixir.Task.Supervisor)
    test_pid = self()

    {:ok, pid} =
      Elixir.Task.Supervisor.start_child(supervisor, fn ->
        :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo, sandbox: false)

        try do
          send(test_pid, {:parent_update_gate_lock_ready, self()})

          receive do
            :attempt_parent_update_gate_lock ->
              %{rows: [[available?]]} =
                Ecto.Adapters.SQL.query!(Repo, "SELECT pg_try_advisory_lock($1)", [gate_key])

              if available? do
                assert %{rows: [[true]]} =
                         Ecto.Adapters.SQL.query!(Repo, "SELECT pg_advisory_unlock($1)", [
                           gate_key
                         ])
              end

              send(test_pid, {:parent_update_gate_lock_available, self(), available?})
          end
        after
          :ok = Ecto.Adapters.SQL.Sandbox.checkin(Repo)
        end
      end)

    ref = Process.monitor(pid)
    assert_receive {:parent_update_gate_lock_ready, ^pid}, 5_000
    send(pid, :attempt_parent_update_gate_lock)
    assert_receive {:parent_update_gate_lock_available, ^pid, available?}, 5_000
    assert_receive {:DOWN, ^ref, :process, ^pid, :normal}, 5_000
    available?
  end

  defp await_parent_update_readiness(workers) do
    workers_by_pid = Map.new(workers, &{&1.pid, &1.ref})
    collect_parent_update_readiness(workers_by_pid, %{})
  end

  defp collect_parent_update_readiness(workers_by_pid, backend_pids)
       when map_size(backend_pids) == map_size(workers_by_pid) do
    backend_pids
  end

  defp collect_parent_update_readiness(workers_by_pid, backend_pids) do
    receive do
      {:parent_update_ready, pid, backend_pid} ->
        if Map.has_key?(workers_by_pid, pid) do
          collect_parent_update_readiness(
            workers_by_pid,
            Map.put(backend_pids, pid, backend_pid)
          )
        else
          flunk("received readiness from an unexpected parent-update worker")
        end

      {:DOWN, _ref, :process, _pid, reason} ->
        flunk("parent-update worker exited before becoming ready: #{inspect(reason)}")
    after
      5_000 -> flunk("timed out waiting for parent-update workers to become ready")
    end
  end

  defp await_parent_update_block(blocked_backend_pid, blocker_backend_pid) do
    deadline = System.monotonic_time(:millisecond) + 5_000
    wait_for_parent_update_block(blocked_backend_pid, blocker_backend_pid, deadline)
  end

  defp wait_for_parent_update_block(blocked_backend_pid, blocker_backend_pid, deadline) do
    if parent_update_blocked_by?(blocked_backend_pid, blocker_backend_pid) do
      :ok
    else
      if System.monotonic_time(:millisecond) < deadline do
        wait_for_parent_update_block(blocked_backend_pid, blocker_backend_pid, deadline)
      else
        flunk("timed out waiting for one parent update to block behind the other")
      end
    end
  end

  defp parent_update_blocked_by?(blocked_backend_pid, blocker_backend_pid) do
    assert %{rows: [[blocked?]]} =
             Ecto.Adapters.SQL.query!(
               Repo,
               "SELECT $2::integer = ANY(pg_blocking_pids($1::integer))",
               [blocked_backend_pid, blocker_backend_pid]
             )

    blocked?
  end

  defp await_parent_update_results(workers) do
    workers_by_pid = Map.new(workers, &{&1.pid, &1.ref})
    collect_parent_update_results(workers_by_pid, %{}, %{})
  end

  defp collect_parent_update_results(workers_by_pid, results, completed_workers)
       when map_size(results) == map_size(workers_by_pid) and
              map_size(completed_workers) == map_size(workers_by_pid) do
    Map.values(results)
  end

  defp collect_parent_update_results(workers_by_pid, results, completed_workers) do
    receive do
      {:parent_update_finished, pid, result} ->
        if Map.has_key?(workers_by_pid, pid) do
          collect_parent_update_results(
            workers_by_pid,
            Map.put(results, pid, result),
            completed_workers
          )
        else
          flunk("received a result from an unexpected parent-update worker")
        end

      {:DOWN, ref, :process, pid, :normal} ->
        if Map.get(workers_by_pid, pid) == ref do
          collect_parent_update_results(
            workers_by_pid,
            results,
            Map.put(completed_workers, pid, true)
          )
        else
          flunk("received a completion signal from an unexpected parent-update worker")
        end

      {:DOWN, _ref, :process, _pid, reason} ->
        flunk("parent-update worker exited unexpectedly: #{inspect(reason)}")
    after
      5_000 -> flunk("timed out waiting for parent-update workers")
    end
  end
end
