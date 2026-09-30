defmodule Taskman.Tasks.BlockingTest do
  use Taskman.DataCase, async: true

  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.BlockingPersistence
  alias Taskman.Tasks.Task

  test "finds a relationship target by exact Task ID across Projects" do
    first_project = project_fixture(%{})
    second_project = project_fixture(%{})
    first = task_fixture(first_project)
    second = task_fixture(second_project)

    assert %Task{id: first_id, project_id: first_project_id} = Tasks.get_task(first.id)
    assert first_id == first.id
    assert first_project_id == first_project.id
    assert %Task{id: second_id} = Tasks.get_task(second.id)
    assert second_id == second.id
    assert Tasks.get_task(0) == nil
    assert Tasks.get_task("invalid") == nil
    assert Tasks.get_task(999_999_999) == nil
  end

  test "reads both directions with named, ordered Task summaries" do
    selected_project = project_fixture(%{name: "Selected"})
    alpha_project = project_fixture(%{name: "alpha"})
    zulu_project = project_fixture(%{name: "Zulu"})
    parent_list = list_fixture(alpha_project, nil, %{name: "Parent"})
    child_list = list_fixture(alpha_project, parent_list, %{name: "Child"})
    selected = task_fixture(selected_project, %{title: "Selected task"})
    alpha = task_fixture(alpha_project, child_list, %{title: "Zebra", priority: :urgent})
    zulu = task_fixture(zulu_project, %{title: "Apple", status: :in_review})
    outgoing = task_fixture(alpha_project, %{title: "Outgoing"})

    assert {:ok, %{blocking_task: blocking, blocked_task: blocked}} =
             Tasks.add_block(alpha_project, alpha, selected)

    assert blocking == %{
             id: alpha.id,
             title: "Zebra",
             status: :pending,
             priority: :urgent,
             project_id: alpha_project.id,
             project_name: "alpha",
             location: %{kind: "list", list_id: child_list.id, path: ["Parent", "Child"]}
           }

    assert blocked.id == selected.id
    assert {:ok, _} = Tasks.add_block(zulu_project, zulu, selected)
    assert {:ok, _} = Tasks.add_block(selected_project, selected, outgoing)

    assert {:ok, %{blocked_by: [first, second], blocks: [target]}} =
             Tasks.list_blocking(selected_project, selected)

    assert Enum.map([first, second], & &1.id) == [alpha.id, zulu.id]
    assert target.id == outgoing.id
    assert second.status == :in_review
    assert blocked.location == %{kind: "project", list_id: nil, path: []}
  end

  test "summary ordering ignores Project and title case and uses IDs as a tie break" do
    project = project_fixture(%{name: "Project"})
    selected = task_fixture(project)
    first = task_fixture(project, %{title: "same"})
    second = task_fixture(project, %{title: "Same"})
    last = task_fixture(project, %{title: "Zulu"})

    for task <- [last, second, first],
        do: assert({:ok, _} = Tasks.add_block(project, selected, task))

    assert {:ok, %{blocks: summaries, blocked_by: []}} = Tasks.list_blocking(project, selected)
    assert Enum.map(summaries, & &1.id) == [first.id, second.id, last.id]
  end

  test "rejects duplicate, self, reverse, and longer cycles without changing edges" do
    project = project_fixture(%{})
    first = task_fixture(project)
    second = task_fixture(project)
    third = task_fixture(project)

    assert {:ok, _} = Tasks.add_block(project, first, second)
    assert {:ok, _} = Tasks.add_block(project, second, third)

    for {source, target, expected} <- [
          {first, second, "already"},
          {first, first, "itself"},
          {second, first, "cycle"},
          {third, first, "cycle"}
        ] do
      assert {:error, changeset} = Tasks.add_block(project, source, target)
      assert Enum.any?(errors_on(changeset).target_task_id, &String.contains?(&1, expected))
    end

    assert Enum.map(BlockingPersistence.outgoing(first.id), & &1.blocked_task_id) == [second.id]
    assert Enum.map(BlockingPersistence.outgoing(second.id), & &1.blocked_task_id) == [third.id]
    assert BlockingPersistence.outgoing(third.id) == []
  end

  test "rejects a cycle whose edges cross Project boundaries" do
    first_project = project_fixture(%{})
    second_project = project_fixture(%{})
    third_project = project_fixture(%{})
    first = task_fixture(first_project)
    second = task_fixture(second_project)
    third = task_fixture(third_project)

    assert {:ok, _} = Tasks.add_block(first_project, first, second)
    assert {:ok, _} = Tasks.add_block(second_project, second, third)
    assert {:error, changeset} = Tasks.add_block(third_project, third, first)
    assert Enum.any?(errors_on(changeset).target_task_id, &String.contains?(&1, "cycle"))
    assert BlockingPersistence.outgoing(third.id) == []
  end

  test "allows a child to block its parent but rejects the opposite direction" do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})
    child = task_fixture(project, %{title: "Child"}, parent: parent)

    assert {:ok, _} = Tasks.add_block(project, child, parent)
    assert {:error, changeset} = Tasks.add_block(project, parent, child)
    assert Enum.any?(errors_on(changeset).target_task_id, &String.contains?(&1, "parent"))
  end

  test "validates scope and stale endpoints before adding or reading" do
    source_project = project_fixture(%{})
    other_project = project_fixture(%{})
    source = task_fixture(source_project)
    target = task_fixture(other_project)
    missing = %Task{id: 9_999_999, project_id: other_project.id}

    assert {:error, :not_found} = Tasks.add_block(other_project, source, target)
    assert {:error, :not_found} = Tasks.add_block(source_project, source, missing)
    assert {:error, :not_found} = Tasks.list_blocking(other_project, source)
    assert BlockingPersistence.outgoing(source.id) == []

    Repo.delete!(target)
    assert {:error, :not_found} = Tasks.add_block(source_project, source, target)
  end

  test "removes only the exact cross-Project edge and refuses a missing edge" do
    source_project = project_fixture(%{})
    target_project = project_fixture(%{})
    source = task_fixture(source_project)
    target = task_fixture(target_project)

    assert {:error, :not_found} = Tasks.remove_block(source_project, source, target)
    assert {:ok, _} = Tasks.add_block(source_project, source, target)

    assert {:ok, %{blocking_task: %{id: source_id}, blocked_task: %{id: target_id}}} =
             Tasks.remove_block(source_project, source, target)

    assert {source_id, target_id} == {source.id, target.id}
    assert {:error, :not_found} = Tasks.remove_block(source_project, source, target)
    assert {:error, :not_found} = Tasks.remove_block(target_project, source, target)
    assert Repo.get(Task, source.id)
    assert Repo.get(Task, target.id)
  end

  test "adding an unresolved blocker to an already Done Task leaves its status Done" do
    project = project_fixture(%{})
    blocker = task_fixture(project, %{status: :pending})
    done_task = task_fixture(project, %{status: :done})

    assert {:ok, _} = Tasks.add_block(project, blocker, done_task)
    assert Repo.get!(Task, done_task.id).status == :done
  end
end
