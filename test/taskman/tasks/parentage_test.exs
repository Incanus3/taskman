defmodule Taskman.Tasks.ParentageTest do
  use Taskman.DataCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.Task

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
