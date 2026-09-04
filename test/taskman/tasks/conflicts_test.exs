defmodule Taskman.Tasks.ConflictsTest do
  use Taskman.DataCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks

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
end
