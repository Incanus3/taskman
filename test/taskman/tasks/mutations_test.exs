defmodule Taskman.Tasks.MutationsTest do
  use Taskman.DataCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.Task

  test "move_task/3 moves a Task among same-Project locations and detects no-op moves" do
    project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me"})

    assert {:ok, moved} = Tasks.move_task(project, task, destination)
    assert moved.list_id == destination.id

    assert {:error, :unchanged_location} = Tasks.move_task(project, moved, destination)

    assert {:ok, moved_direct} = Tasks.move_task(project, moved, nil)
    assert moved_direct.list_id == nil
  end

  test "move_task/3 rejects foreign or stale Tasks and destinations" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Planning"})
    foreign_destination = list_fixture(other_project, nil, %{name: "Foreign"})
    task = task_fixture(project, %{title: "Move me"})
    foreign_task = task_fixture(other_project, %{title: "Foreign task"})

    assert {:error, :not_found} = Tasks.move_task(project, task, foreign_destination)
    assert {:error, :not_found} = Tasks.move_task(project, foreign_task, destination)

    Repo.delete!(destination)
    assert {:error, :not_found} = Tasks.move_task(project, task, destination)

    Repo.delete!(task)
    assert {:error, :not_found} = Tasks.move_task(project, task, nil)
  end

  test "get_task_for_project/2 returns only a Task owned by the Project" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    task = task_fixture(project, %{title: "Owned"})
    other_task = task_fixture(other_project, %{title: "Other"})

    assert Tasks.get_task_for_project(project, task.id) == task
    assert Tasks.get_task_for_project(project, Integer.to_string(task.id)) == task
    assert Tasks.get_task_for_project(project, other_task.id) == nil
    assert Tasks.get_task_for_project(project, "not-an-id") == nil
    assert Tasks.get_task_for_project(project, -1) == nil
    assert Tasks.get_task_for_project(project, 999_999_999) == nil
  end

  test "update_task/3 persists every editable field" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before"})
    due_at = ~N[2026-08-03 16:00:00]

    attrs = %{
      title: "  After  ",
      description: "Updated",
      status: :in_review,
      priority: :urgent,
      due_at: due_at
    }

    assert {:ok, updated} = Tasks.update_task(project, task, attrs)
    assert updated.title == "After"
    assert updated.description == "Updated"
    assert updated.status == :in_review
    assert updated.priority == :urgent
    assert updated.due_at == due_at
  end

  test "update_task/3 rejects a blank title without changing the persisted Task" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Original title"})

    assert {:error, changeset} = Tasks.update_task(project, task, %{title: ""})
    assert %{title: [_]} = errors_on(changeset)
    assert Tasks.get_task_for_project(project, task.id).title == "Original title"
  end

  test "update_task/3 keeps ownership immutable and rejects a mismatched Project" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    task = task_fixture(project, %{title: "Owned"})

    assert {:ok, updated} =
             Tasks.update_task(project, task, %{
               title: "Still owned",
               project_id: other_project.id
             })

    assert updated.project_id == project.id
    assert {:error, :not_found} = Tasks.update_task(other_project, task, %{title: "Leaked"})
    assert Tasks.get_task_for_project(project, task.id).title == "Still owned"
  end

  test "update_task/3 accepts every fixed lifecycle status" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Lifecycle"})

    Enum.reduce(Task.statuses(), task, fn status, current ->
      assert {:ok, updated} = Tasks.update_task(project, current, %{status: status})
      assert updated.status == status
      updated
    end)
  end

  test "update_task/3 accepts every fixed priority" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Priority"})

    Enum.reduce(Task.priorities(), task, fn priority, current ->
      assert {:ok, updated} = Tasks.update_task(project, current, %{priority: priority})
      assert updated.priority == priority
      updated
    end)
  end
end
