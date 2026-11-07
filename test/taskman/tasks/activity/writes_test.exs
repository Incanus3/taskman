defmodule Taskman.Tasks.Activity.WritesTest do
  use Taskman.DataCase, async: false

  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures
  import Taskman.ListsFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.Activity.Event

  setup do
    %{project: project_fixture(%{name: "Work"}), actor: user_fixture()}
  end

  test "creation with an initial parent records one attributed creation on the child", %{
    project: project,
    actor: actor
  } do
    parent = task_fixture(project)

    assert {:ok, task} =
             Tasks.create_task(project, nil, %{title: "Child", priority: :high},
               actor: actor,
               parent: parent
             )

    assert task.parent_task_id == parent.id
    assert {:ok, [%{change: change}]} = Tasks.list_activity(project, task)
    assert change.type == "task_created"
    assert change.actor.user_id == actor.id
    assert change.before == nil
    assert change.after == nil
    assert change.first_changed_at == change.last_changed_at
  end

  test "successful edits record normalized scalar values and owning List transitions", %{
    project: project,
    actor: actor
  } do
    task = task_fixture(project, %{title: "Before", priority: :low})
    list = list_fixture(project, %{name: "Planning"})

    assert {:ok, updated} =
             Tasks.update_task(
               project,
               task,
               %{
                 title: "  After  ",
                 status: "in_progress",
                 priority: "high",
                 due_at: "2030-02-03T04:05:06"
               }, actor: actor)

    assert {:ok, moved} = Tasks.move_task(project, updated, list, actor: actor)
    assert moved.lock_version == task.lock_version + 2
    changes = field_changes(project, moved)

    assert Enum.map(changes, & &1.field) |> Enum.sort() == [
             "due_at",
             "list_id",
             "priority",
             "status",
             "title"
           ]

    assert Enum.find(changes, &(&1.field == "title")).after == "After"
    assert Enum.find(changes, &(&1.field == "status")).before == "pending"
    assert Enum.find(changes, &(&1.field == "priority")).after == "high"
    assert Enum.find(changes, &(&1.field == "due_at")).after == "2030-02-03T04:05:06"
    location = Enum.find(changes, &(&1.field == "list_id"))
    assert location.before.list_id == nil
    assert location.after.path == ["Planning"]
  end

  test "description saves persist without recording prose even alongside field edits", %{
    project: project,
    actor: actor
  } do
    task = task_fixture(project)

    assert {:ok, described} =
             Tasks.update_task(project, task, %{description: "Private prose"}, actor: actor)

    assert field_changes(project, described) == []

    assert {:ok, mixed} =
             Tasks.update_task(
               project,
               described,
               %{description: "More private prose", priority: :high}, actor: actor)

    assert mixed.description == "More private prose"
    assert [%{field: "priority"}] = field_changes(project, mixed)
    refute inspect(Repo.all(from e in Event, where: e.task_id == ^task.id)) =~ "private prose"
  end

  test "missing and stale ineligible actors cannot write any public Task mutation", %{
    project: project,
    actor: actor
  } do
    task = task_fixture(project)
    other = task_fixture(project)

    Repo.update_all(from(u in Taskman.Accounts.User, where: u.id == ^actor.id),
      set: [status: :disabled]
    )

    for opts <- [[], [actor: actor], [actor: %{id: actor.id}]] do
      assert {:error, :authentication_required} =
               Tasks.create_task(project, nil, %{title: "Rejected"}, opts)

      assert {:error, :authentication_required} =
               Tasks.update_task(project, task, %{title: "Rejected"}, opts)

      assert {:error, :authentication_required} = Tasks.move_task(project, task, nil, opts)
      assert {:error, :authentication_required} = Tasks.add_block(project, task, other, opts)
      assert {:error, :authentication_required} = Tasks.remove_block(project, task, other, opts)
    end

    assert Tasks.get_task(task.id).title == task.title
  end

  test "rejections and convergent stale retries leave the existing event unchanged", %{
    project: project,
    actor: actor
  } do
    task = task_fixture(project, %{title: "Before"})
    child = task_fixture(project, %{}, parent: task)
    assert {:ok, titled} = Tasks.update_task(project, task, %{title: "After"}, actor: actor)
    event = Repo.get_by!(Event, task_id: task.id, field: "title")
    assert {:ok, converged} = Tasks.update_task(project, task, %{title: "After"}, actor: actor)
    assert converged.lock_version == titled.lock_version

    assert {:error, %Taskman.Tasks.Conflict{}} =
             Tasks.update_task(project, task, %{title: "Conflicting"}, actor: actor)

    assert {:error, %Ecto.Changeset{}} =
             Tasks.update_task(project, titled, %{title: ""}, actor: actor)

    assert {:error, {:unresolved_blockers, [%{id: child_id}]}} =
             Tasks.update_task(project, titled, %{status: :done}, actor: actor)

    assert child_id == child.id
    assert Repo.get!(Event, event.id) == event
    assert [%{field: "title"}] = field_changes(project, titled)
  end

  test "event constraint failure rolls back a successful Task update", %{
    project: project,
    actor: actor
  } do
    task = task_fixture(project, %{title: "Before"})

    Ecto.Adapters.SQL.query!(
      Repo,
      "ALTER TABLE task_activity_events ADD CONSTRAINT reject_test_activity CHECK (field IS DISTINCT FROM 'title')"
    )

    assert_raise Ecto.ConstraintError, fn ->
      Tasks.update_task(project, task, %{title: "After"}, actor: actor)
    end

    assert Tasks.get_task(task.id).title == "Before"
    assert field_changes(project, task) == []
  end

  test "public writes reject caller transactions before mutation", %{
    project: project,
    actor: actor
  } do
    task = task_fixture(project)
    other = task_fixture(project)

    for operation <- [
          fn -> Tasks.create_task(project, nil, %{title: "Rejected"}, actor: actor) end,
          fn -> Tasks.update_task(project, task, %{title: "Rejected"}, actor: actor) end,
          fn -> Tasks.move_task(project, task, nil, actor: actor) end,
          fn -> Tasks.add_block(project, task, other, actor: actor) end,
          fn -> Tasks.remove_block(project, task, other, actor: actor) end
        ] do
      assert {:ok, :ok} =
               Repo.transaction(fn ->
                 assert_raise ArgumentError, ~r/outside.*transaction/, operation
                 :ok
               end)
    end

    assert Tasks.get_task(task.id) == task
    assert length(Tasks.list_tasks_for_project(project)) == 2
  end

  test "legacy create and update conveniences require an explicit actor", %{project: project} do
    task = task_fixture(project)
    assert {:error, :authentication_required} = Tasks.create_task(project)
    assert {:error, :authentication_required} = Tasks.create_task(project, %{title: "Rejected"})

    assert {:error, :authentication_required} =
             Tasks.create_task(project, nil, %{title: "Rejected"})

    assert {:error, :authentication_required} =
             Tasks.update_task(project, task, %{title: "Rejected"})
  end

  test "history failure rolls back creation with its initial parent", %{
    project: project,
    actor: actor
  } do
    parent = task_fixture(project)

    Ecto.Adapters.SQL.query!(
      Repo,
      "ALTER TABLE task_activity_events ADD CONSTRAINT reject_test_creation CHECK (kind IS DISTINCT FROM 'task_created') NOT VALID"
    )

    assert_raise Ecto.ConstraintError, fn ->
      Tasks.create_task(project, nil, %{title: "Rejected child"}, parent: parent, actor: actor)
    end

    assert Tasks.list_tasks_for_project(project) == [parent]
    assert Repo.aggregate(Event, :count) == 1
  end

  test "write callback errors roll back its mutations", %{project: project, actor: actor} do
    task = task_fixture(project)

    assert {:error, :rejected} =
             Taskman.Tasks.Writes.run(actor, fn persisted_actor ->
               assert persisted_actor.id == actor.id
               task |> Ecto.Changeset.change(title: "Temporary") |> Repo.update!()
               {:error, :rejected}
             end)

    assert Tasks.get_task(task.id).title == task.title
  end

  defp field_changes(project, task) do
    {:ok, entries} = Tasks.list_activity(project, task)
    entries |> Enum.map(& &1.change) |> Enum.filter(&(&1.type == "field_changed"))
  end
end
