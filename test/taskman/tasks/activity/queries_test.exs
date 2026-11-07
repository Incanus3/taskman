defmodule Taskman.Tasks.Activity.QueriesTest do
  use Taskman.DataCase, async: true

  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.Activity
  alias Taskman.Tasks.Activity.Event
  alias Taskman.Tasks.Comment

  setup do
    project = project_fixture(%{})
    %{project: project, task: historical_task_fixture(project), actor: user_fixture()}
  end

  test "scopes the selected Task against persisted Project ownership", %{
    project: project,
    task: task
  } do
    assert {:ok, []} = Tasks.list_activity(project, task)
    assert {:error, :not_found} = Tasks.list_activity(project_fixture(%{}), task)
    assert {:error, :not_found} = Tasks.list_activity(project, %{task | id: nil})
    Repo.delete!(task)
    assert {:error, :not_found} = Tasks.list_activity(project, task)
  end

  test "events are default and mixed timelines use effective time, kind and numeric ID", %{
    project: project,
    task: task,
    actor: actor
  } do
    {:ok, {:ok, _}} = Repo.transaction(fn -> Activity.record_task(actor, nil, task, []) end)
    created = Repo.one!(Event)

    {:ok, {:ok, _}} =
      Repo.transaction(fn ->
        Activity.record_task(actor, task, %{task | title: "Renamed"}, [:title])
      end)

    changed = Repo.get_by!(Event, kind: "field_changed")
    time = ~U[2030-01-01 10:00:00.000000Z]
    Repo.update_all(Event, set: [first_changed_at: time, last_changed_at: time])

    comment =
      Repo.insert!(%Comment{
        task_id: task.id,
        actor_user_id: actor.id,
        text: "Hello",
        created_at: DateTime.truncate(time, :second)
      })

    later_comment =
      Repo.insert!(%Comment{
        task_id: task.id,
        text: "Later",
        created_at: DateTime.add(DateTime.truncate(time, :second), 1, :second)
      })

    assert {:ok, events} = Tasks.list_activity(project, task)
    assert Enum.map(events, & &1.id) == ["event-#{created.id}", "event-#{changed.id}"]
    assert {:ok, entries} = Tasks.list_activity(project, task, include_comments: true)

    assert Enum.map(entries, & &1.id) == [
             "comment-#{comment.id}",
             "event-#{created.id}",
             "event-#{changed.id}",
             "comment-#{later_comment.id}"
           ]

    assert hd(entries).kind == :comment
    assert hd(events).kind == :change
    assert hd(events).occurred_at == time

    Repo.update_all(from(e in Event, where: e.id == ^created.id),
      set: [last_changed_at: DateTime.add(time, 2, :second)]
    )

    assert {:ok, reordered} = Tasks.list_activity(project, task, include_comments: true)
    assert List.last(reordered).id == "event-#{created.id}"
  end

  test "actors resolve their current login and deleted accounts retain no login snapshot", %{
    project: project,
    task: task,
    actor: actor
  } do
    {:ok, {:ok, _}} = Repo.transaction(fn -> Activity.record_task(actor, nil, task, []) end)

    Ecto.Adapters.SQL.query!(Repo, "UPDATE users SET email = $1 WHERE id = $2", [
      "renamed@example.com",
      Ecto.UUID.dump!(actor.id)
    ])

    assert {:ok, [%{change: %{actor: %{user_id: actor_id, login: "renamed@example.com"}}}]} =
             Tasks.list_activity(project, task)

    assert actor_id == actor.id
    stored = Repo.one!(Event)
    refute Jason.encode!(stored.context) =~ "@"
    Ecto.Adapters.SQL.query!(Repo, "DELETE FROM users WHERE id = $1", [Ecto.UUID.dump!(actor.id)])

    assert {:ok, [%{change: %{actor: %{user_id: nil, login: "Deleted user"}}}]} =
             Tasks.list_activity(project, task)
  end

  test "missing reference targets retain historical labels and use one batched lookup", %{
    project: project,
    task: task,
    actor: actor
  } do
    parent = historical_task_fixture(project, %{title: "Historic parent"})

    {:ok, {:ok, _}} =
      Repo.transaction(fn ->
        Activity.record_task(actor, %{task | parent_task_id: parent.id}, task, [:parent_task_id])
      end)

    {:ok, {:ok, _}} =
      Repo.transaction(fn ->
        Activity.record_task(actor, task, %{task | priority: :high}, [:priority])
      end)

    Repo.delete!(parent)
    test_pid = self()
    handler_id = "activity-query-#{System.unique_integer([:positive])}"

    :telemetry.attach(
      handler_id,
      [:taskman, :repo, :query],
      fn _, _, meta, _ -> if self() == test_pid, do: send(test_pid, {:query, meta.query}) end,
      nil
    )

    on_exit(fn -> :telemetry.detach(handler_id) end)
    assert {:ok, entries} = Tasks.list_activity(project, task)
    assert length(entries) == 2
    change = Enum.find(entries, &(&1.change.field == "parent_task_id")).change
    assert change.before.id == parent.id
    assert change.before.title == "Historic parent"
    assert change.before.available? == false
    assert change.context.task.available? == true
    assert length(collect_queries()) == 4
  end

  defp collect_queries(acc \\ []) do
    receive do
      {:query, query} -> collect_queries([query | acc])
    after
      0 -> acc
    end
  end

  # These storage/query tests model Tasks that predate activity recording.
  defp historical_task_fixture(project, attrs \\ %{}) do
    task = Taskman.TasksFixtures.task_fixture(project, attrs)
    Repo.delete_all(from e in Event, where: e.task_id == ^task.id)
    task
  end
end
