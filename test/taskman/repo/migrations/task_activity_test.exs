defmodule Taskman.Repo.Migrations.TaskActivityTest do
  use Taskman.DataCase, async: true

  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures

  alias Ecto.Adapters.SQL
  alias Taskman.Tasks.Activity
  alias Taskman.Tasks.Activity.{Event, EventTask}

  test "deleting an associated Task removes only its event association; deleting the owner removes the event" do
    project = project_fixture(%{})
    owner = historical_task_fixture(project)
    parent = historical_task_fixture(project)
    actor = user_fixture()

    {:ok, {:ok, _}} =
      Repo.transaction(fn ->
        Activity.record_task(actor, owner, %{owner | parent_task_id: parent.id}, [:parent_task_id])
      end)

    event = Repo.one!(Event)
    assert Repo.aggregate(EventTask, :count) == 2
    Repo.delete!(parent)
    assert Repo.get(Event, event.id)
    assert Repo.aggregate(EventTask, :count) == 1
    Repo.delete!(owner)
    assert Repo.all(Event) == []
    assert Repo.all(EventTask) == []
  end

  test "database enforces event shape, wrapped values, time order and positive IDs" do
    task = historical_task_fixture(project_fixture(%{}))

    cases = [
      {"kind", "unknown", "task_activity_events_kind_field_check"},
      {"field", "description", "task_activity_events_kind_field_check"},
      {"before_value", %{}, "task_activity_events_values_check"},
      {"context", %{}, "task_activity_events_context_check"},
      {"last_changed_at", ~N[2029-01-01 00:00:00], "task_activity_events_time_check"},
      {"id", 0, "task_activity_events_id_positive_check"}
    ]

    for {column, value, constraint} <- cases do
      attrs = %{
        id: 9_000_000 + System.unique_integer([:positive]),
        task_id: task.id,
        grouping_key: "task:#{task.id}:title",
        kind: "field_changed",
        field: "title",
        before_value: %{"value" => "Old"},
        after_value: %{"value" => "New"},
        context: %{"task" => %{"id" => task.id}},
        first_changed_at: ~N[2030-01-01 00:00:00],
        last_changed_at: ~N[2030-01-01 00:00:00]
      }

      attrs = Map.put(attrs, String.to_existing_atom(column), value)

      error =
        assert_raise Postgrex.Error, fn ->
          Repo.transaction(
            fn ->
              SQL.query!(
                Repo,
                "INSERT INTO task_activity_events (id, task_id, grouping_key, kind, field, before_value, after_value, context, first_changed_at, last_changed_at) VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8::jsonb, $9::timestamp, $10::timestamp)",
                [
                  attrs.id,
                  attrs.task_id,
                  attrs.grouping_key,
                  attrs.kind,
                  attrs.field,
                  attrs.before_value,
                  attrs.after_value,
                  attrs.context,
                  attrs.first_changed_at,
                  attrs.last_changed_at
                ]
              )
            end,
            mode: :savepoint
          )
        end

      assert error.postgres.constraint == constraint
    end
  end

  test "event/Task pairs are unique and time columns retain microseconds" do
    task = historical_task_fixture(project_fixture(%{}))

    {:ok, {:ok, _}} =
      Repo.transaction(fn -> Activity.record_task(user_fixture(), nil, task, []) end)

    event = Repo.one!(Event)
    exact = ~U[2030-01-01 10:00:00.123456Z]
    Repo.update_all(Event, set: [first_changed_at: exact, last_changed_at: exact])
    assert Repo.one!(Event).last_changed_at == exact
    assert Event.__schema__(:type, :last_changed_at) == :utc_datetime_usec

    assert_raise Ecto.ConstraintError, fn ->
      Repo.transaction(fn -> Repo.insert!(%EventTask{event_id: event.id, task_id: task.id}) end,
        mode: :savepoint
      )
    end
  end

  defp historical_task_fixture(project) do
    task = Taskman.TasksFixtures.task_fixture(project)
    Repo.delete_all(from e in Event, where: e.task_id == ^task.id)
    task
  end
end
