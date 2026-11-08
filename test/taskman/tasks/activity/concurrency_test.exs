defmodule Taskman.Tasks.Activity.ConcurrencyTest do
  use Taskman.DataCase, async: false

  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Ecto.Adapters.SQL
  alias Ecto.Adapters.SQL.Sandbox
  alias Taskman.Accounts.User
  alias Taskman.{ChangeNotifications, Tasks}
  alias Taskman.Projects.Project
  alias Taskman.Tasks.Activity.Event

  test "simultaneous first field events preserve both writes and the successful retry context" do
    with_state(fn project, task, first_actor, second_actor, supervisor ->
      first =
        worker(supervisor, fn ->
          hold_task_write(fn ->
            Tasks.update_task(project, task, %{title: "Renamed"}, actor: first_actor)
          end)
        end)

      assert_receive {:write_held, first_pid}, 5_000
      assert first_pid == first.pid

      second =
        worker(supervisor, fn ->
          Tasks.update_task(project, task, %{priority: :high}, actor: second_actor)
        end)

      assert_blocked(second)
      send(first.pid, :release_write)
      assert {:ok, titled} = result(first)
      assert {:ok, merged} = result(second)
      assert merged.lock_version == titled.lock_version + 1
      assert merged.title == "Renamed"
      assert merged.priority == :high

      assert [%{before_value: %{"value" => "Before"}, after_value: %{"value" => "Renamed"}}] =
               events(task, "title")

      assert [
               %{
                 before_value: %{"value" => "low"},
                 after_value: %{"value" => "high"},
                 context: %{"task" => %{"title" => "Renamed"}}
               }
             ] = events(task, "priority")
    end)
  end

  test "concurrent convergent stale requests produce one event and one version increment" do
    with_state(fn project, task, first_actor, second_actor, supervisor ->
      first =
        worker(supervisor, fn ->
          hold_task_write(fn ->
            Tasks.update_task(project, task, %{title: "After"}, actor: first_actor)
          end)
        end)

      assert_receive {:write_held, _}, 5_000

      second =
        worker(supervisor, fn ->
          Tasks.update_task(project, task, %{title: "After"}, actor: second_actor)
        end)

      assert_blocked(second)
      send(first.pid, :release_write)
      assert {:ok, updated} = result(first)
      assert {:ok, ^updated} = result(second)
      assert updated.lock_version == task.lock_version + 1
      assert [%{actor_user_id: actor_id}] = events(task, "title")
      assert actor_id == first_actor.id
    end)
  end

  test "same-field sequential history starts each event at the preceding committed value" do
    with_state(fn project, task, first_actor, second_actor, supervisor ->
      first =
        worker(supervisor, fn ->
          hold_task_write(fn ->
            Tasks.update_task(project, task, %{priority: :medium}, actor: first_actor)
          end)
        end)

      assert_receive {:write_held, _}, 5_000
      # A fresh read taken after the first commit must be the next mutation's baseline.
      second =
        worker(supervisor, fn ->
          receive do: (:write_next -> :ok)
          current = Tasks.get_task(task.id)
          Tasks.update_task(project, current, %{priority: :urgent}, actor: second_actor)
        end)

      send(first.pid, :release_write)
      assert {:ok, _} = result(first)
      send(second.pid, :write_next)
      assert {:ok, final} = result(second)
      assert final.priority == :urgent

      assert Enum.map(
               events(task, "priority"),
               &{&1.before_value["value"], &1.after_value["value"]}
             ) == [{"low", "medium"}, {"medium", "urgent"}]
    end)
  end

  test "actor deletion wins its lock before a queued edit and rejects the edit" do
    with_state(fn project, task, actor, _other, supervisor ->
      deletion =
        worker(supervisor, fn ->
          Repo.transaction(fn ->
            delete_actor(actor)
            send(Process.get(:test_pid), {:deletion_held, self()})
            receive do: (:finish_deletion -> :ok)
          end)
        end)

      assert_receive {:deletion_held, _}, 5_000

      edit =
        worker(supervisor, fn ->
          Tasks.update_task(project, task, %{title: "Rejected"}, actor: actor)
        end)

      assert_blocked(edit)
      send(deletion.pid, :finish_deletion)
      assert {:ok, :ok} = result(deletion)
      assert {:error, :authentication_required} = result(edit)
      assert Tasks.get_task(task.id).title == "Before"
      assert events(task, "title") == []
    end)
  end

  test "an edit holding its actor lock commits before deletion nullifies the event author" do
    with_state(fn project, task, actor, _other, supervisor ->
      edit =
        worker(supervisor, fn ->
          hold_task_write(fn ->
            Tasks.update_task(project, task, %{title: "After"}, actor: actor)
          end)
        end)

      assert_receive {:write_held, _}, 5_000
      deletion = worker(supervisor, fn -> delete_actor(actor) end)
      assert_blocked(deletion)
      send(edit.pid, :release_write)
      assert {:ok, _} = result(edit)
      assert %Postgrex.Result{} = result(deletion)
      assert [%{actor_user_id: nil, after_value: %{"value" => "After"}}] = events(task, "title")
      assert {:ok, entries} = Tasks.list_activity(project, task)

      assert Enum.find(entries, &(&1.change.field == "title")).change.actor.login ==
               "Deleted user"
    end)
  end

  test "activity invalidations are emitted only after committed history is readable" do
    with_state(fn project, task, actor, _other, supervisor ->
      assert :ok = ChangeNotifications.subscribe_project(project)

      edit =
        worker(supervisor, fn ->
          hold_task_write(fn ->
            Tasks.update_task(project, task, %{title: "After"}, actor: actor)
          end)
        end)

      assert_receive {:write_held, _}, 5_000
      assert Tasks.get_task(task.id).title == "Before"
      assert events(task, "title") == []
      refute_receive %ChangeNotifications.Event{entity: :activity}, 0
      send(edit.pid, :release_write)

      assert_receive %ChangeNotifications.Event{
                       entity: :activity,
                       operation: :invalidated,
                       task_ids: ids,
                       fields: []
                     },
                     5_000

      assert ids == [task.id]
      assert [%{after_value: %{"value" => "After"}}] = events(task, "title")
      assert Tasks.get_task(task.id).title == "After"
      assert {:ok, _} = result(edit)
    end)
  end

  test "publication failure leaves committed history readable" do
    previous = Application.get_env(:taskman, :change_notifications_pubsub)
    Application.put_env(:taskman, :change_notifications_pubsub, :missing_activity_pubsub)

    on_exit(fn ->
      if previous,
        do: Application.put_env(:taskman, :change_notifications_pubsub, previous),
        else: Application.delete_env(:taskman, :change_notifications_pubsub)
    end)

    project = project_fixture(%{})
    actor = user_fixture()
    assert {:ok, task} = Tasks.create_task(project, nil, %{title: "Committed"}, actor: actor)
    assert {:ok, [%{change: %{type: "task_created"}}]} = Tasks.list_activity(project, task)
  end

  defp cleanup_state(project, actor_ids) do
    Sandbox.unboxed_run(Repo, fn ->
      Repo.delete_all(from p in Project, where: p.id == ^project.id)
      Repo.delete_all(from u in User, where: u.id in ^actor_ids)
    end)
  end

  defp with_state(fun) do
    Sandbox.unboxed_run(Repo, fn ->
      project = project_fixture(%{})
      first = user_fixture()
      second = user_fixture()
      task = task_fixture(project, %{title: "Before", priority: :low}, actor: first)

      on_exit(fn -> cleanup_state(project, [first.id, second.id]) end)

      fun.(project, task, first, second, start_supervised!(Task.Supervisor))
    end)
  end

  defp events(task, field),
    do:
      Repo.all(
        from e in Event, where: e.task_id == ^task.id and e.field == ^field, order_by: e.id
      )

  defp delete_actor(actor),
    do:
      SQL.query!(Repo, "DELETE FROM users WHERE id = $1", [
        Ecto.UUID.dump!(actor.id)
      ])

  defp worker(supervisor, fun) do
    test_pid = self()

    {:ok, pid} =
      Task.Supervisor.start_child(supervisor, fn ->
        :ok = Sandbox.checkout(Repo, sandbox: false)
        Process.put(:test_pid, test_pid)

        try do
          %{rows: [[backend]]} = SQL.query!(Repo, "SELECT pg_backend_pid()")
          send(test_pid, {:backend, self(), backend})
          send(test_pid, {:result, self(), fun.()})
        after
          Sandbox.checkin(Repo)
        end
      end)

    %{pid: pid, ref: Process.monitor(pid)}
  end

  defp result(%{pid: pid, ref: ref}) do
    assert_receive {:result, ^pid, result}, 5_000
    assert_receive {:DOWN, ^ref, :process, ^pid, :normal}, 5_000
    result
  end

  defp hold_task_write(fun) do
    handler = {__MODULE__, make_ref()}
    owner = self()
    test_pid = Process.get(:test_pid)

    :ok =
      :telemetry.attach(
        handler,
        [:taskman, :repo, :query],
        fn _, _, meta, _ ->
          if self() == owner and String.starts_with?(meta.query, ~s(UPDATE "tasks")) do
            send(test_pid, {:write_held, self()})
            receive do: (:release_write -> :ok)
          end
        end,
        nil
      )

    try do: fun.(), after: :telemetry.detach(handler)
  end

  defp assert_blocked(%{pid: pid}) do
    assert_receive {:backend, ^pid, backend}, 5_000
    wait_blocked(backend, System.monotonic_time(:millisecond) + 5_000)
  end

  defp wait_blocked(backend, deadline) do
    %{rows: [[blockers]]} =
      SQL.query!(Repo, "SELECT pg_blocking_pids($1)", [backend])

    cond do
      blockers != [] -> :ok
      System.monotonic_time(:millisecond) < deadline -> wait_blocked(backend, deadline)
      true -> flunk("writer did not block on the held database lock")
    end
  end
end
