defmodule Taskman.Tasks.Activity.PersistenceTest do
  use Taskman.DataCase, async: true

  import Taskman.AccountsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures

  alias Taskman.Tasks.Activity
  alias Taskman.Tasks.Activity.{Event, EventTask}
  alias Taskman.Tasks.Task

  setup do
    project = project_fixture(%{name: "Work"})

    %{
      project: project,
      task: historical_task_fixture(project, %{priority: :low, description: "Private prose"}),
      actor: user_fixture()
    }
  end

  test "creation records only the created Task with no initial-field entries", %{
    project: project,
    task: task,
    actor: actor
  } do
    parent = historical_task_fixture(project)
    task = %{task | parent_task_id: parent.id}
    {:ok, affected_tasks} = record(actor, nil, task, [:title, :priority])
    assert affected_tasks == [{task.project_id, task.id}]

    assert [
             %Event{
               kind: "task_created",
               field: nil,
               before_value: %{"value" => nil},
               after_value: %{"value" => nil}
             } = event
           ] = Repo.all(Event)

    assert event.first_changed_at == event.last_changed_at
    assert event.context["task"]["title"] == task.title
    refute Jason.encode!(event.context) =~ "Private prose"
  end

  test "scalar transitions retain identity and original time and cancel on return", %{
    task: task,
    actor: actor
  } do
    middle = %{task | priority: :medium}
    assert {:ok, [_]} = record(actor, task, middle, [:priority])
    original = Repo.one!(Event)
    assert {:ok, [_]} = record(actor, middle, %{task | priority: :high}, [:priority])
    updated = Repo.one!(Event)
    assert updated.id == original.id
    assert updated.first_changed_at == original.first_changed_at
    assert updated.before_value == %{"value" => "low"}
    assert updated.after_value == %{"value" => "high"}
    assert {:ok, [_]} = record(actor, %{task | priority: :high}, task, [:priority])
    assert Repo.all(Event) == []
    assert Repo.all(EventTask) == []
  end

  test "unchanged and description-only transitions leave last change untouched", %{
    task: task,
    actor: actor
  } do
    middle = %{task | priority: :medium}
    record(actor, task, middle, [:priority])
    event = Repo.one!(Event)
    assert {:ok, []} = record(actor, middle, middle, [:priority])

    assert {:ok, []} =
             record(actor, middle, %{middle | description: "New private prose"}, [:description])

    assert {:ok, []} = record(actor, middle, middle, [])
    assert Repo.one!(Event).last_changed_at == event.last_changed_at
  end

  test "a surviving other-user entry prevents merging backwards", %{task: task, actor: actor} do
    other = user_fixture()
    medium = %{task | priority: :medium}
    high = %{task | priority: :high}
    record(actor, task, medium, [:priority])
    record(other, medium, high, [:priority])
    record(actor, high, %{task | priority: :urgent}, [:priority])
    assert Repo.aggregate(Event, :count) == 3
  end

  test "cancelling an intervening user's event exposes the latest surviving candidate", %{
    task: task,
    actor: actor
  } do
    other = user_fixture()
    medium = %{task | priority: :medium}
    high = %{task | priority: :high}
    record(actor, task, medium, [:priority])
    first = Repo.one!(Event)
    at_zero = DateTime.add(DateTime.utc_now(), -30, :second)
    Repo.update_all(Event, set: [first_changed_at: at_zero, last_changed_at: at_zero])
    record(other, medium, high, [:priority])
    at_ten = DateTime.add(at_zero, 10, :second)

    Repo.update_all(from(e in Event, where: e.id != ^first.id),
      set: [first_changed_at: at_ten, last_changed_at: at_ten]
    )

    record(other, high, medium, [:priority])
    assert [%{id: surviving_id}] = Repo.all(Event)
    assert surviving_id == first.id
    record(actor, medium, %{task | priority: :urgent}, [:priority])

    assert [
             %{
               id: final_id,
               before_value: %{"value" => "low"},
               after_value: %{"value" => "urgent"},
               first_changed_at: ^at_zero
             }
           ] = Repo.all(Event)

    assert final_id == first.id
  end

  test "parent consolidation preserves original snapshot, replaces final snapshot and coordinates associated Tasks",
       %{project: project, task: task, actor: actor} do
    a = historical_task_fixture(project, %{title: "Original"})
    b = historical_task_fixture(project, %{title: "Intermediate"})
    c = historical_task_fixture(project, %{title: "Final"})
    original = %{task | parent_task_id: a.id}
    intermediate = %{task | parent_task_id: b.id}
    record(actor, original, intermediate, [:parent_task_id])
    event = Repo.one!(Event)
    Repo.update_all(from(t in Task, where: t.id == ^a.id), set: [title: "Renamed"])

    {:ok, affected} =
      record(actor, intermediate, %{task | parent_task_id: c.id, title: "New child title"}, [
        :parent_task_id
      ])

    assert MapSet.new(affected) == MapSet.new(Enum.map([task, a, b, c], &{project.id, &1.id}))
    updated = Repo.one!(Event)
    assert updated.id == event.id

    assert updated.before_value["value"] == %{
             "id" => a.id,
             "project_id" => project.id,
             "project_name" => "Work",
             "title" => "Original"
           }

    assert updated.after_value["value"]["id"] == c.id
    assert updated.context["task"]["title"] == "New child title"
    assert event_task_ids() == Enum.sort([task.id, a.id, c.id])
    {:ok, _} = record(actor, %{task | parent_task_id: c.id}, original, [:parent_task_id])
    assert Repo.all(Event) == []
  end

  test "List labels and paths are snapshots and IDs control equality", %{
    project: project,
    task: task,
    actor: actor
  } do
    root = list_fixture(project, %{name: "Root"})
    child = list_fixture(project, root, %{name: "Child"})
    placed = %{task | list_id: child.id}
    record(actor, task, placed, [:list_id])
    event = Repo.one!(Event)
    assert event.before_value["value"]["list_id"] == nil
    assert event.after_value["value"]["path"] == ["Root", "Child"]

    Repo.update_all(from(l in Taskman.Lists.TaskList, where: l.id == ^child.id),
      set: [name: "Renamed"]
    )

    record(actor, placed, task, [:list_id])
    assert Repo.all(Event) == []
  end

  test "blocking uses directed pair identity across Projects and cancellation removes both event/Task associations",
       %{task: task, actor: actor} do
    target =
      historical_task_fixture(project_fixture(%{name: "Elsewhere"}), %{
        description: "Do not capture"
      })

    edge = %{
      blocking_task: Map.put(Map.from_struct(task), :project_name, "Work"),
      blocked_task: Map.put(Map.from_struct(target), :project_name, "Elsewhere")
    }

    assert {:ok, {:ok, affected}} =
             Repo.transaction(fn -> Activity.record_block(actor, edge, false, true) end)

    assert MapSet.new(affected) ==
             MapSet.new([{task.project_id, task.id}, {target.project_id, target.id}])

    event = Repo.one!(Event)
    assert event.grouping_key == "block:#{task.id}:#{target.id}"
    assert event.after_value == %{"value" => true}
    assert event.context["blocked_task"]["title"] == target.title
    refute Jason.encode!(event.context) =~ "Do not capture"

    assert {:ok, {:ok, []}} =
             Repo.transaction(fn -> Activity.record_block(actor, edge, true, true) end)

    assert {:ok, {:ok, _}} =
             Repo.transaction(fn -> Activity.record_block(actor, edge, true, false) end)

    assert Repo.all(Event) == []
    assert Repo.all(EventTask) == []
  end

  test "mixed field writes exclude description and normalize due dates", %{
    task: task,
    actor: actor
  } do
    changed = %{
      task
      | description: "Secret new content",
        due_at: ~N[2030-02-03 04:05:06],
        status: :done,
        title: "Updated"
    }

    record(actor, task, changed, [:description, :due_at, :status, :title])
    assert Enum.sort(Enum.map(Repo.all(Event), & &1.field)) == ["due_at", "status", "title"]
    due = Repo.get_by!(Event, field: "due_at")
    assert due.after_value == %{"value" => "2030-02-03T04:05:06"}
    refute Jason.encode!(Enum.map(Repo.all(Event), & &1.context)) =~ "Secret new content"
  end

  test "latest candidate uses effective time and numeric ID rather than first-change order", %{
    task: task,
    actor: actor
  } do
    other = user_fixture()
    medium = %{task | priority: :medium}
    high = %{task | priority: :high}
    record(actor, task, medium, [:priority])
    first = Repo.one!(Event)
    record(other, medium, high, [:priority])
    latest = Repo.get_by!(Event, actor_user_id: other.id)
    time = DateTime.add(DateTime.utc_now(), -10, :second)
    Repo.update_all(Event, set: [first_changed_at: time, last_changed_at: time])
    record(actor, high, %{task | priority: :urgent}, [:priority])
    assert Repo.aggregate(Event, :count) == 3
    assert Repo.get!(Event, first.id).after_value == %{"value" => "medium"}
    assert Repo.get!(Event, latest.id).after_value == %{"value" => "high"}
  end

  test "expired events remain separate and unrelated fields do not interrupt consolidation", %{
    task: task,
    actor: actor
  } do
    medium = %{task | priority: :medium}
    record(actor, task, medium, [:priority])
    old = DateTime.add(DateTime.utc_now(), -61, :second)
    Repo.update_all(Event, set: [first_changed_at: old, last_changed_at: old])
    record(actor, medium, %{task | priority: :high}, [:priority])
    assert Repo.aggregate(Event, :count) == 2
    record(actor, task, %{task | title: "Changed title"}, [:title])
    record(actor, %{task | priority: :high}, %{task | priority: :urgent}, [:priority])
    assert Repo.aggregate(Event, :count) == 3
    persisted = Repo.get!(Task, task.id)
    assert persisted.updated_at == task.updated_at
    assert persisted.lock_version == task.lock_version
  end

  test "consolidation retains a deleted original parent snapshot without recreating its event association",
       %{project: project, task: task, actor: actor} do
    original_parent = historical_task_fixture(project, %{title: "Former parent"})
    middle_parent = historical_task_fixture(project)
    final_parent = historical_task_fixture(project)
    before = %{task | parent_task_id: original_parent.id}
    middle = %{task | parent_task_id: middle_parent.id}
    record(actor, before, middle, [:parent_task_id])
    Repo.delete!(original_parent)

    assert {:ok, affected} =
             record(actor, middle, %{task | parent_task_id: final_parent.id}, [:parent_task_id])

    assert MapSet.new(affected) ==
             MapSet.new(Enum.map([task, middle_parent, final_parent], &{project.id, &1.id}))

    assert Repo.one!(Event).before_value["value"]["title"] == "Former parent"
    assert event_task_ids() == Enum.sort([task.id, final_parent.id])
  end

  defp record(actor, before, after_task, fields) do
    {:ok, result} =
      Repo.transaction(fn -> Activity.record_task(actor, before, after_task, fields) end)

    result
  end

  defp event_task_ids, do: Repo.all(from a in EventTask, order_by: a.task_id, select: a.task_id)
  # These storage/query tests model Tasks that predate activity recording.
  defp historical_task_fixture(project, attrs \\ %{}) do
    task = Taskman.TasksFixtures.task_fixture(project, attrs)
    Repo.delete_all(from e in Event, where: e.task_id == ^task.id)
    task
  end
end
