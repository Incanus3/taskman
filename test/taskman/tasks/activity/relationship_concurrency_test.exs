defmodule Taskman.Tasks.Activity.RelationshipConcurrencyTest do
  use Taskman.DataCase, async: false

  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Ecto.Adapters.SQL
  alias Ecto.Adapters.SQL.Sandbox
  alias Taskman.Accounts.User
  alias Taskman.{ChangeNotifications, Tasks}
  alias Taskman.Projects.Project
  alias Taskman.Tasks.Activity.{Event, Persistence}
  alias Taskman.Tasks.BlockingPersistence

  test "same actor serializes add and remove before a fresh transition after cancellation" do
    with_state(fn project, source, target, actor, _other, supervisor ->
      first =
        worker(supervisor, fn ->
          hold_event(fn -> Tasks.add_block(project, source, target, actor: actor) end)
        end)

      assert_receive {:history_held, _}, 5_000

      second =
        worker(supervisor, fn -> Tasks.remove_block(project, source, target, actor: actor) end)

      assert_blocked(second, first)
      refute_receive %ChangeNotifications.Event{entity: :activity}, 0
      released_at = DateTime.utc_now()
      send(first.pid, :release_history)
      assert {:ok, _} = result(first)
      assert {:ok, _} = result(second)
      assert block_events(source) == []
      assert {:ok, entries} = Tasks.list_activity(project, target)
      assert Enum.all?(entries, &(&1.change.type == "task_created"))

      third = worker(supervisor, fn -> Tasks.add_block(project, source, target, actor: actor) end)
      assert {:ok, _} = result(third)
      assert [event] = block_events(source)
      assert event.before_value == %{"value" => false}
      assert event.after_value == %{"value" => true}
      assert DateTime.compare(event.first_changed_at, released_at) in [:eq, :gt]
      assert_event_tasks(project, event, [source, target])
      assert Tasks.get_task(source.id) == source
      assert Tasks.get_task(target.id) == target
    end)
  end

  test "different actors hold the graph lock through history and sample queued transition time after it" do
    with_state(fn project, source, target, actor, other, supervisor ->
      first =
        worker(supervisor, fn ->
          hold_event(fn -> Tasks.add_block(project, source, target, actor: actor) end)
        end)

      assert_receive {:history_held, _}, 5_000

      second =
        worker(supervisor, fn -> Tasks.remove_block(project, source, target, actor: other) end)

      assert_blocked(second, first)
      assert block_events(source) == []
      refute_receive %ChangeNotifications.Event{entity: :activity}, 0
      released_at = DateTime.utc_now()
      send(first.pid, :release_history)
      assert {:ok, _} = result(first)
      assert {:ok, _} = result(second)
      assert [added, removed] = block_events(source)
      assert {added.actor_user_id, removed.actor_user_id} == {actor.id, other.id}

      assert {removed.before_value, removed.after_value} ==
               {%{"value" => true}, %{"value" => false}}

      assert DateTime.compare(removed.first_changed_at, released_at) in [:eq, :gt]
      assert_event_tasks(project, added, [source, target])
      assert_event_tasks(project, removed, [source, target])
    end)
  end

  test "different actors converging on a parent preserve one canonical event and one Task update" do
    with_state(fn project, child, parent, actor, other, supervisor ->
      first =
        worker(supervisor, fn ->
          hold_event(fn ->
            Tasks.update_task(project, child, %{}, parent: parent, actor: actor)
          end)
        end)

      assert_receive {:history_held, _}, 5_000

      second =
        worker(supervisor, fn ->
          Tasks.update_task(project, child, %{}, parent: parent, actor: other)
        end)

      assert_blocked(second, first)
      assert Tasks.get_task(child.id) == child
      send(first.pid, :release_history)
      assert {:ok, updated} = result(first)
      assert {:ok, ^updated} = result(second)
      assert updated.lock_version == child.lock_version + 1

      assert [event] =
               Repo.all(
                 from e in Event, where: e.task_id == ^child.id and e.field == "parent_task_id"
               )

      assert event.actor_user_id == actor.id
      assert event.before_value == %{"value" => nil}
      assert event.after_value["value"]["id"] == parent.id
      assert_event_tasks(project, event, [child, parent])
      assert Tasks.get_task(parent.id) == parent
    end)
  end

  test "cancelled parent candidate can be followed by a queued new transition" do
    with_state(fn project, child, parent, actor, _other, supervisor ->
      assert {:ok, attached} =
               Tasks.update_task(project, child, %{}, parent: parent, actor: actor)

      first_event = Repo.get_by!(Event, task_id: child.id, field: "parent_task_id")

      first =
        worker(supervisor, fn ->
          hold_event(
            fn -> Tasks.update_task(project, attached, %{}, parent: nil, actor: actor) end,
            :delete
          )
        end)

      assert_receive {:history_held, _}, 5_000

      second =
        worker(supervisor, fn ->
          # Passing the final baseline of the first workflow exercises the newly empty
          # candidate set while its transaction still owns the actor/graph locks.
          expected = %{attached | parent_task_id: nil, lock_version: attached.lock_version + 1}
          Tasks.update_task(project, expected, %{}, parent: parent, actor: actor)
        end)

      assert_blocked(second, first)
      send(first.pid, :release_history)
      assert {:ok, _} = result(first)
      assert {:ok, final} = result(second)

      assert [event] =
               Repo.all(
                 from e in Event, where: e.task_id == ^child.id and e.field == "parent_task_id"
               )

      assert event.id != first_event.id
      assert event.before_value == %{"value" => nil}
      assert event.after_value["value"]["id"] == parent.id
      assert final.lock_version == child.lock_version + 3
      assert_event_tasks(project, event, [child, parent])
    end)
  end

  test "history failure rolls back the graph before a queued writer validates it" do
    with_state(fn project, source, target, actor, other, supervisor ->
      SQL.query!(
        Repo,
        "ALTER TABLE task_activity_events ADD CONSTRAINT reject_queued_relationship CHECK (actor_user_id <> '#{actor.id}'::uuid) NOT VALID"
      )

      try do
        first =
          worker(supervisor, fn ->
            hold_query(
              fn ->
                try do
                  Tasks.add_block(project, source, target, actor: actor)
                rescue
                  Ecto.ConstraintError -> :history_rejected
                end
              end,
              ~s(INSERT INTO "task_blocking_links")
            )
          end)

        assert_receive {:history_held, _}, 5_000

        second =
          worker(supervisor, fn -> Tasks.add_block(project, source, target, actor: other) end)

        assert_blocked(second, first)
        assert block_events(source) == []
        refute_receive %ChangeNotifications.Event{entity: :activity}, 0
        send(first.pid, :release_history)
        assert :history_rejected = result(first)
        assert {:ok, _} = result(second)
        assert [event] = block_events(source)
        assert event.actor_user_id == other.id
        assert_event_tasks(project, event, [source, target])
        assert BlockingPersistence.edge?(source.id, target.id)
      after
        SQL.query!(
          Repo,
          "ALTER TABLE task_activity_events DROP CONSTRAINT reject_queued_relationship"
        )
      end
    end)
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
      actor = user_fixture()
      other = user_fixture()
      source = task_fixture(project, %{}, actor: actor)
      target = task_fixture(project, %{}, actor: actor)

      on_exit(fn -> cleanup_state(project, [actor.id, other.id]) end)

      :ok = ChangeNotifications.subscribe_project(project)
      fun.(project, source, target, actor, other, start_supervised!(Task.Supervisor))
    end)
  end

  defp block_events(source),
    do:
      Repo.all(
        from e in Event,
          where: e.task_id == ^source.id and e.kind == "block_changed",
          order_by: e.id
      )

  defp assert_event_tasks(project, event, tasks) do
    assert MapSet.new(Persistence.event_tasks(event.id)) ==
             MapSet.new(Enum.map(tasks, &{project.id, &1.id}))
  end

  defp worker(supervisor, fun) do
    test_pid = self()

    {:ok, pid} =
      Task.Supervisor.start_child(supervisor, fn ->
        :ok = Sandbox.checkout(Repo, sandbox: false)
        Process.put(:test_pid, test_pid)

        try do
          %{rows: [[backend]]} = SQL.query!(Repo, "SELECT pg_backend_pid()")
          send(test_pid, {:backend, self(), backend})

          receive do
            :run -> send(test_pid, {:result, self(), fun.()})
          end
        after
          Sandbox.checkin(Repo)
        end
      end)

    assert_receive {:backend, ^pid, backend}, 5_000
    ref = Process.monitor(pid)
    send(pid, :run)
    %{pid: pid, ref: ref, backend: backend}
  end

  defp result(%{pid: pid, ref: ref}) do
    assert_receive {:result, ^pid, result}, 5_000
    assert_receive {:DOWN, ^ref, :process, ^pid, :normal}, 5_000
    result
  end

  defp hold_event(fun, operation \\ :insert) do
    prefix =
      if operation == :insert,
        do: ~s(INSERT INTO "task_activity_events"),
        else: ~s(DELETE FROM "task_activity_events")

    hold_query(fun, prefix)
  end

  defp hold_query(fun, prefix) do
    handler = {__MODULE__, make_ref()}
    owner = self()
    test_pid = Process.get(:test_pid)

    :ok =
      :telemetry.attach(
        handler,
        [:taskman, :repo, :query],
        fn _, _, meta, _ ->
          if self() == owner and String.starts_with?(meta.query, prefix) do
            send(test_pid, {:history_held, self()})

            receive do
              :release_history -> :ok
            after
              5_000 -> raise "history gate timed out"
            end
          end
        end,
        nil
      )

    try do: fun.(), after: :telemetry.detach(handler)
  end

  defp assert_blocked(worker, owner),
    do: wait_blocked(worker.backend, owner.backend, System.monotonic_time(:millisecond) + 5_000)

  defp wait_blocked(backend, owner, deadline) do
    %{rows: [[blockers]]} =
      SQL.query!(Repo, "SELECT pg_blocking_pids($1)", [backend])

    cond do
      owner in blockers -> :ok
      System.monotonic_time(:millisecond) < deadline -> wait_blocked(backend, owner, deadline)
      true -> flunk("relationship writer did not block on the held owner's lock")
    end
  end
end
