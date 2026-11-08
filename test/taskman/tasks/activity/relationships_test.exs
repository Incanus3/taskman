defmodule Taskman.Tasks.Activity.RelationshipsTest do
  use Taskman.DataCase, async: false

  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Ecto.Adapters.SQL
  alias Taskman.Tasks
  alias Taskman.Tasks.{Activity, BlockingPersistence, Mutations, Writes}
  alias Taskman.Tasks.Activity.Event

  setup do
    %{project: project_fixture(%{name: "Work"}), actor: user_fixture()}
  end

  test "reparent consolidation keeps original and final associated Tasks and invalidates the intermediate",
       %{
         project: project,
         actor: actor
       } do
    original = task_fixture(project, %{title: "Original"})
    middle = task_fixture(project, %{title: "Intermediate"})
    final = task_fixture(project, %{title: "Final"})
    child = task_fixture(project, %{title: "Child"}, parent: original, actor: actor)
    assert changes(project, original) == []

    assert {:ok, [%{change: %{type: "task_created", task_id: original_id}}]} =
             Tasks.list_activity(project, original)

    assert original_id == original.id

    assert {:ok, changed} = Tasks.update_task(project, child, %{}, parent: middle, actor: actor)
    [first] = changes(project, child)

    assert {:ok, {{persisted, [:parent_task_id]}, affected}} =
             Writes.run(actor, fn locked_actor ->
               Writes.task(locked_actor, fn ->
                 Mutations.update(project, changed, %{}, parent: final)
               end)
             end)

    assert MapSet.new(affected) ==
             MapSet.new(Enum.map([child, original, middle, final], &{project.id, &1.id}))

    assert [event] = changes(project, child)
    assert event.id == first.id
    assert event.change.before.id == original.id
    assert event.change.after.id == final.id
    assert event.change.context.task.title == "Child"
    assert changes(project, original) == [event]
    assert changes(project, final) == [event]
    assert changes(project, middle) == []
    assert persisted.lock_version == child.lock_version + 2
    assert Tasks.get_task(original.id) == original
    assert Tasks.get_task(middle.id) == middle
    assert Tasks.get_task(final.id) == final

    assert {:ok, cancelled} =
             Tasks.update_task(project, persisted, %{}, parent: original, actor: actor)

    assert cancelled.lock_version == persisted.lock_version + 1
    assert changes(project, child) == []
    assert Repo.all(from e in Event, where: e.field == "parent_task_id") == []
    assert changes(project, original) == []
    assert changes(project, final) == []
  end

  test "parent removal and different users retain distinct canonical events", %{
    project: project,
    actor: actor
  } do
    first = task_fixture(project)
    second = task_fixture(project)
    child = task_fixture(project, %{}, parent: first)
    other = user_fixture()
    assert {:ok, moved} = Tasks.update_task(project, child, %{}, parent: second, actor: actor)
    assert {:ok, removed} = Tasks.update_task(project, moved, %{}, parent: nil, actor: other)
    assert [earlier, later] = changes(project, removed)
    assert earlier.change.before.id == first.id
    assert earlier.change.after.id == second.id
    assert later.change.before.id == second.id
    assert later.change.after == nil
    assert later.change.actor.user_id == other.id
    assert changes(project, first) == [earlier]
    assert changes(project, second) == [earlier, later]
  end

  test "deleted original parent keeps historical labels while final event associations use surviving Tasks",
       %{project: project, actor: actor} do
    original = task_fixture(project, %{title: "Historical parent"})
    middle = task_fixture(project)
    final = task_fixture(project)
    child = task_fixture(project, %{}, parent: original)
    assert {:ok, changed} = Tasks.update_task(project, child, %{}, parent: middle, actor: actor)
    Repo.delete!(original)
    assert {:ok, changed} = Tasks.update_task(project, changed, %{}, parent: final, actor: actor)
    assert [event] = changes(project, changed)
    assert event.change.before.title == "Historical parent"
    refute event.change.before.available?
    assert event.change.after.available?
    assert changes(project, middle) == []
    assert changes(project, final) == [event]
  end

  test "cross-Project blocks share one directed event and cancel from either endpoint", %{
    project: project,
    actor: actor
  } do
    other_project = project_fixture(%{name: "Other"})
    source = task_fixture(project, %{title: "Source"})
    target = task_fixture(other_project, %{title: "Target"})

    assert {:ok, %{blocking_task: source_summary, blocked_task: target_summary}} =
             Tasks.add_block(project, source, target, actor: actor)

    assert source_summary.id == source.id
    assert target_summary.id == target.id
    assert [event] = changes(project, source)
    assert changes(other_project, target) == [event]
    assert event.change.type == "block_changed"
    assert event.change.task_id == source.id
    assert event.change.before == false
    assert event.change.after == true
    assert event.change.context.blocking_task.project_name == "Work"
    assert event.change.context.blocked_task.project_name == "Other"

    # The incoming endpoint UI resolves the same directed pair for removal.
    assert {:ok, _} = Tasks.remove_block(project, source, target, actor: actor)
    assert changes(project, source) == []
    assert changes(other_project, target) == []
    assert Tasks.get_task(source.id) == source
    assert Tasks.get_task(target.id) == target

    assert {:ok, _} = Tasks.add_block(other_project, target, source, actor: actor)
    assert [reverse] = changes(project, source)
    assert reverse.change.task_id == target.id
    assert reverse.change.context.blocked_task.id == source.id
    assert reverse.id != event.id
  end

  test "block pairs consolidate independently and different users do not cancel each other's event",
       %{project: project, actor: actor} do
    source = task_fixture(project)
    first = task_fixture(project)
    second = task_fixture(project)
    other = user_fixture()
    assert {:ok, _} = Tasks.add_block(project, source, first, actor: actor)
    assert {:ok, _} = Tasks.add_block(project, source, second, actor: actor)
    assert {:ok, _} = Tasks.remove_block(project, source, first, actor: other)
    assert [added, removed] = changes(project, first)
    assert added.change.after == true
    assert removed.change.before == true
    assert removed.change.after == false
    assert removed.change.actor.user_id == other.id
    assert [_] = changes(project, second)
    assert length(changes(project, source)) == 3
  end

  test "graph validation failures preserve graph and relationship history", %{
    project: project,
    actor: actor
  } do
    parent = task_fixture(project)
    child = task_fixture(project, %{}, parent: parent)
    target = task_fixture(project)
    assert {:error, %Ecto.Changeset{}} = Tasks.add_block(project, parent, child, actor: actor)

    assert {:error, %Ecto.Changeset{}} =
             Tasks.update_task(project, parent, %{}, parent: child, actor: actor)

    assert {:ok, _} = Tasks.add_block(project, child, target, actor: actor)
    before = changes(project, child)
    assert {:error, %Ecto.Changeset{}} = Tasks.add_block(project, target, child, actor: actor)

    assert {:error, %Ecto.Changeset{}} =
             Tasks.update_task(project, target, %{}, parent: child, actor: actor)

    assert changes(project, child) == before
    assert Tasks.get_task(target.id).parent_task_id == nil
    assert BlockingPersistence.edge?(child.id, target.id)
    refute BlockingPersistence.edge?(target.id, child.id)
  end

  test "event failure rolls back block add, remove and parent mutation", %{
    project: project,
    actor: actor
  } do
    source = task_fixture(project)
    target = task_fixture(project)
    parent = task_fixture(project)
    assert {:ok, _} = Tasks.add_block(project, source, target, actor: actor)
    before = changes(project, source)

    SQL.query!(
      Repo,
      "ALTER TABLE task_activity_events ADD CONSTRAINT reject_relationship_history CHECK (kind != 'block_changed' AND field IS DISTINCT FROM 'parent_task_id') NOT VALID"
    )

    assert_raise Ecto.ConstraintError, fn ->
      Tasks.add_block(project, target, parent, actor: actor)
    end

    refute BlockingPersistence.edge?(target.id, parent.id)
    # Use another actor so removal inserts rather than deletes the first actor's event.
    other = user_fixture()

    assert_raise Ecto.ConstraintError, fn ->
      Tasks.remove_block(project, source, target, actor: other)
    end

    assert BlockingPersistence.edge?(source.id, target.id)

    assert_raise Ecto.ConstraintError, fn ->
      Tasks.update_task(project, target, %{}, parent: parent, actor: actor)
    end

    assert Tasks.get_task(target.id) == target
    assert changes(project, source) == before
  end

  defp changes(project, task) do
    {:ok, entries} = Activity.list(project, task)
    Enum.reject(entries, &(&1.change.type == "task_created"))
  end
end
