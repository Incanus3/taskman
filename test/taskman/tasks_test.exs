defmodule Taskman.TasksTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures

  alias Taskman.Tasks

  test "create_task/2 assigns ownership and product defaults" do
    project = project_fixture(%{})

    assert {:ok, task} = Tasks.create_task(project, %{title: "First task"})
    assert task.project_id == project.id
    assert task.title == "First task"
    assert task.description == ""
    assert task.status == :pending
    assert task.priority == :none
  end

  test "create_task/2 persists an explicitly empty description as an empty string" do
    project = project_fixture(%{})

    assert {:ok, task} =
             Tasks.create_task(project, %{title: "Empty description", description: ""})

    assert task.description == ""
  end

  test "create_task/2 requires a title and ignores user-owned project IDs" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})

    assert {:error, changeset} = Tasks.create_task(project, %{title: ""})
    assert %{title: [_]} = errors_on(changeset)

    assert {:ok, task} =
             Tasks.create_task(project, %{title: "Owned safely", project_id: other_project.id})

    assert task.project_id == project.id
  end

  test "list_tasks_for_project/1 returns only that Project's tasks in stable order" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    {:ok, first} = Tasks.create_task(project, %{title: "First"})
    {:ok, second} = Tasks.create_task(project, %{title: "Second"})
    {:ok, _other} = Tasks.create_task(other_project, %{title: "Other"})

    assert Tasks.list_tasks_for_project(project) == [first, second]
  end
end
