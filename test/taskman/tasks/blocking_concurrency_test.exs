defmodule Taskman.Tasks.BlockingConcurrencyTest do
  use Taskman.DataCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.BlockingPersistence
  alias Taskman.Tasks.Task

  @graph_lock_key {724_150, 1}

  test "opposite link adds serialize and cannot form a two-Task cycle" do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      project = project_fixture(%{})
      first = task_fixture(project)
      second = task_fixture(project)
      cleanup_project(project)

      [forward, reverse] =
        run_behind_graph_gate([
          fn -> Tasks.add_block(project, first, second) end,
          fn -> Tasks.add_block(project, second, first) end
        ])

      assert Enum.count([forward, reverse], &match?({:ok, _}, &1)) == 1
      assert Enum.count([forward, reverse], &cycle_error?/1) == 1

      edges = BlockingPersistence.outgoing(first.id) ++ BlockingPersistence.outgoing(second.id)
      assert length(edges) == 1
    end)
  end

  test "link addition and reparenting serialize without a forbidden parent-to-child edge" do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      project = project_fixture(%{})
      parent = task_fixture(project, %{title: "Parent"})
      child = task_fixture(project, %{title: "Before"})
      cleanup_project(project)

      [link_result, parent_result] =
        run_behind_graph_gate([
          fn -> Tasks.add_block(project, parent, child) end,
          fn -> Tasks.update_task(project, child, %{title: "After"}, parent: parent) end
        ])

      assert Enum.count([link_result, parent_result], &match?({:ok, _}, &1)) == 1
      assert Enum.count([link_result, parent_result], &parent_direction_error?/1) == 1

      persisted_child = Repo.get!(Task, child.id)
      link_exists? = BlockingPersistence.edge?(parent.id, child.id)
      refute link_exists? and persisted_child.parent_task_id == parent.id

      if link_exists? do
        assert persisted_child.title == "Before"
        assert persisted_child.parent_task_id == nil
      else
        assert persisted_child.title == "After"
        assert persisted_child.parent_task_id == parent.id
      end
    end)
  end

  test "relationship read skips an endpoint deleted after loading its edge" do
    Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
      project = project_fixture(%{})
      source = task_fixture(project)
      target = task_fixture(project)
      assert {:ok, _} = Tasks.add_block(project, source, target)
      cleanup_project(project)

      test_pid = self()
      handler_id = {__MODULE__, make_ref()}
      gate = make_ref()
      query_gate = :atomics.new(1, [])

      on_exit(fn -> :telemetry.detach(handler_id) end)

      :ok =
        :telemetry.attach(
          handler_id,
          [:taskman, :repo, :query],
          fn _event, _measurements, %{query: query, params: params}, _config ->
            if params == [source.id] and
                 String.contains?(query, ~s(FROM "task_blocking_links")) and
                 String.contains?(query, ~s("blocking_task_id")) and
                 :atomics.compare_exchange(query_gate, 1, 0, 1) == :ok do
              send(test_pid, {:outgoing_edge_loaded, self()})

              receive do
                {:continue_relationship_read, ^gate} -> :ok
              after
                5_000 -> raise "timed out waiting to continue relationship read"
              end
            end
          end,
          nil
        )

      supervisor = start_supervised!(Elixir.Task.Supervisor)

      {:ok, reader} =
        Elixir.Task.Supervisor.start_child(supervisor, fn ->
          :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo, sandbox: false)

          try do
            send(
              test_pid,
              {:relationship_read_result, self(), Tasks.list_blocking(project, source)}
            )
          after
            :ok = Ecto.Adapters.SQL.Sandbox.checkin(Repo)
          end
        end)

      ref = Process.monitor(reader)
      assert_receive {:outgoing_edge_loaded, ^reader}, 5_000
      Repo.delete!(target)
      send(reader, {:continue_relationship_read, gate})

      assert_receive {:relationship_read_result, ^reader, {:ok, %{blocks: [], blocked_by: []}}},
                     5_000

      assert_receive {:DOWN, ^ref, :process, ^reader, :normal}, 5_000
    end)
  end

  defp run_behind_graph_gate(operations) do
    {first_key, second_key} = @graph_lock_key
    Ecto.Adapters.SQL.query!(Repo, "SELECT pg_advisory_lock($1, $2)", [first_key, second_key])

    try do
      owner_backend = database_backend_pid()
      supervisor = start_supervised!(Elixir.Task.Supervisor)
      test_pid = self()

      workers =
        Enum.map(operations, fn operation ->
          {:ok, pid} =
            Elixir.Task.Supervisor.start_child(supervisor, fn ->
              :ok = Ecto.Adapters.SQL.Sandbox.checkout(Repo, sandbox: false)

              try do
                send(test_pid, {:graph_worker_ready, self(), database_backend_pid()})

                receive do
                  :start_graph_write ->
                    send(test_pid, {:graph_worker_result, self(), operation.()})
                end
              after
                :ok = Ecto.Adapters.SQL.Sandbox.checkin(Repo)
              end
            end)

          %{pid: pid, ref: Process.monitor(pid)}
        end)

      backends =
        Map.new(workers, fn %{pid: pid} ->
          assert_receive {:graph_worker_ready, ^pid, backend}, 5_000
          {pid, backend}
        end)

      Enum.each(workers, fn %{pid: pid} -> send(pid, :start_graph_write) end)

      Enum.each(workers, fn %{pid: pid} ->
        await_blocked(Map.fetch!(backends, pid), owner_backend)
      end)

      assert %{rows: [[true]]} =
               Ecto.Adapters.SQL.query!(Repo, "SELECT pg_advisory_unlock($1, $2)", [
                 first_key,
                 second_key
               ])

      Enum.map(workers, fn %{pid: pid, ref: ref} ->
        assert_receive {:graph_worker_result, ^pid, result}, 5_000
        assert_receive {:DOWN, ^ref, :process, ^pid, :normal}, 5_000
        result
      end)
    after
      Ecto.Adapters.SQL.query!(Repo, "SELECT pg_advisory_unlock($1, $2)", [first_key, second_key])
    end
  end

  defp await_blocked(backend, owner_backend) do
    deadline = System.monotonic_time(:millisecond) + 5_000
    poll_blocked(backend, owner_backend, deadline)
  end

  defp poll_blocked(backend, owner_backend, deadline) do
    %{rows: [[blockers]]} =
      Ecto.Adapters.SQL.query!(Repo, "SELECT pg_blocking_pids($1::integer)", [backend])

    cond do
      owner_backend in blockers ->
        :ok

      blockers != [] ->
        :ok

      System.monotonic_time(:millisecond) < deadline ->
        poll_blocked(backend, owner_backend, deadline)

      true ->
        flunk("graph writer did not wait for the lock")
    end
  end

  defp database_backend_pid do
    %{rows: [[backend]]} = Ecto.Adapters.SQL.query!(Repo, "SELECT pg_backend_pid()")
    backend
  end

  defp cycle_error?({:error, %Ecto.Changeset{} = changeset}) do
    Enum.any?(Map.get(errors_on(changeset), :target_task_id, []), &String.contains?(&1, "cycle"))
  end

  defp cycle_error?(_), do: false

  defp parent_direction_error?({:error, %Ecto.Changeset{} = changeset}) do
    errors = errors_on(changeset)

    Enum.any?(
      Map.get(errors, :target_task_id, []) ++ Map.get(errors, :parent_task_id, []),
      fn message ->
        String.contains?(message, "parent") or String.contains?(message, "blocks")
      end
    )
  end

  defp parent_direction_error?(_), do: false

  defp cleanup_project(project) do
    on_exit(fn ->
      Ecto.Adapters.SQL.Sandbox.unboxed_run(Repo, fn ->
        if persisted = Repo.get(Taskman.Projects.Project, project.id), do: Repo.delete!(persisted)
      end)
    end)
  end
end
