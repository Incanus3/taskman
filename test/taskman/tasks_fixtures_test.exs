defmodule Taskman.TasksFixturesTest do
  use Taskman.DataCase, async: true

  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Accounts.User
  alias Taskman.Tasks

  setup do
    %{project: project_fixture(%{})}
  end

  test "write helpers persist Tasks and blocking relationships with default actors", %{
    project: project
  } do
    list = list_fixture(project)

    assert {:ok, task} = create_task(project, nil, %{title: "Original"})
    assert Tasks.get_task(task.id) == task
    assert {:ok, updated} = update_task(project, task, %{title: "Updated"})
    assert Tasks.get_task(task.id) == updated
    assert {:ok, moved} = move_task(project, updated, list)
    assert Tasks.get_task(task.id).list_id == list.id

    other = task_fixture(project)

    assert {:ok, %{blocking_task: blocker, blocked_task: blocked}} =
             add_block(project, moved, other)

    assert blocker.id == task.id
    assert blocked.id == other.id
    assert {:ok, %{blocks: [%{id: blocked_id}]}} = Tasks.list_blocking(project, moved)
    assert blocked_id == other.id

    assert {:ok, %{blocking_task: ^blocker, blocked_task: ^blocked}} =
             remove_block(project, moved, other)

    assert {:ok, %{blocks: []}} = Tasks.list_blocking(project, moved)
  end

  test "explicit actors own the recorded activity without creating another account", %{
    project: project
  } do
    actor = user_fixture()
    user_count = Repo.aggregate(User, :count)
    task = task_fixture(project, %{title: "Original"}, actor: actor)
    assert {:ok, updated} = update_task(project, task, %{title: "Updated"}, actor: actor)

    assert Repo.aggregate(User, :count) == user_count
    assert {:ok, entries} = Tasks.list_activity(project, updated)
    assert length(entries) == 2
    assert Enum.all?(entries, &(&1.change.actor.user_id == actor.id))
  end

  test "default actors keep consecutive edits separate in activity history", %{project: project} do
    task = task_fixture(project, %{title: "Original"})
    assert {:ok, first} = update_task(project, task, %{title: "First"})
    assert {:ok, second} = update_task(project, first, %{title: "Second"})

    assert {:ok, entries} = Tasks.list_activity(project, second)
    changes = Enum.filter(entries, &(&1.change.field == "title"))
    assert Enum.map(changes, & &1.change.after) == ["First", "Second"]
    assert [first_actor_id, second_actor_id] = Enum.map(changes, & &1.change.actor.user_id)
    assert is_binary(first_actor_id)
    assert is_binary(second_actor_id)
    refute first_actor_id == second_actor_id
  end

  test "explicit missing and ineligible actors reject writes without fixture side effects", %{
    project: project
  } do
    list = list_fixture(project)
    task = task_fixture(project, list, %{title: "Original"})
    other = task_fixture(project)
    assert {:ok, _} = add_block(project, task, other)
    pending_actor = pending_user_fixture()
    user_count = Repo.aggregate(User, :count)
    event_count = Repo.aggregate(Tasks.Activity.Event, :count)

    for actor <- [nil, false, pending_actor] do
      assert {:error, :authentication_required} =
               create_task(project, nil, %{title: "Rejected"}, actor: actor)

      assert {:error, :authentication_required} =
               update_task(project, task, %{title: "Rejected"}, actor: actor)

      assert {:error, :authentication_required} = move_task(project, task, nil, actor: actor)
      assert {:error, :authentication_required} = add_block(project, other, task, actor: actor)
      assert {:error, :authentication_required} = remove_block(project, task, other, actor: actor)
    end

    assert Repo.aggregate(User, :count) == user_count
    assert Repo.aggregate(Tasks.Activity.Event, :count) == event_count
    assert Tasks.get_task(task.id) == task
    assert Tasks.list_tasks_for_project(project) == [task, other]

    assert {:ok, %{blocks: [%{id: blocked_id}], blocked_by: []}} =
             Tasks.list_blocking(project, task)

    assert blocked_id == other.id
  end

  test "creation and updates pass relationship options through to the context", %{
    project: project
  } do
    parent = task_fixture(project)

    assert {:ok, child} = create_task(project, nil, %{title: "Child"}, parent: parent)
    assert child.parent_task_id == parent.id
    assert {:ok, detached} = update_task(project, child, %{}, parent: nil)
    assert detached.parent_task_id == nil
    assert Tasks.get_task(child.id).parent_task_id == nil
  end

  test "validation errors remain context results", %{project: project} do
    assert {:error, changeset} = create_task(project, nil, %{title: ""})
    assert %{title: ["can't be blank"]} = errors_on(changeset)
    assert Tasks.list_tasks_for_project(project) == []
  end
end
