defmodule Taskman.Tasks.CountPersistenceTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks.CountPersistence

  @unclosed [:icebox, :pending, :in_progress, :in_review]

  test "returns direct count pairs and parent IDs for every Project location" do
    project = project_fixture(%{})
    root = list_fixture(project)
    child = list_fixture(project, root)
    empty = list_fixture(project)
    closed = list_fixture(project)
    task_fixture(project, %{status: :in_review})
    task_fixture(project, %{status: :pending})
    task_fixture(project, %{status: :done})
    task_fixture(project, root, %{status: :in_review})
    task_fixture(project, child, %{status: :in_review})
    task_fixture(project, child, %{status: :icebox})
    task_fixture(project, closed, %{status: :done})
    task_fixture(project, closed, %{status: :will_not_do})
    other_project = project_fixture(%{})
    task_fixture(other_project, %{status: :in_review})
    task_fixture(other_project, list_fixture(other_project), %{status: :in_review})

    rows = CountPersistence.list_location_counts(project.id, [:in_review], @unclosed)

    assert length(rows) == 5
    rows_by_id = Map.new(rows, &{&1.list_id, &1})

    assert rows_by_id[nil] == %{
             list_id: nil,
             parent_list_id: nil,
             visible_count: 1,
             eligible_count: 2
           }

    assert rows_by_id[root.id] == %{
             list_id: root.id,
             parent_list_id: nil,
             visible_count: 1,
             eligible_count: 1
           }

    assert rows_by_id[child.id] == %{
             list_id: child.id,
             parent_list_id: root.id,
             visible_count: 1,
             eligible_count: 2
           }

    for list <- [empty, closed] do
      assert rows_by_id[list.id] == %{
               list_id: list.id,
               parent_list_id: nil,
               visible_count: 0,
               eligible_count: 0
             }
    end
  end

  test "an empty Project still returns exactly one zero Project row" do
    project = project_fixture(%{})

    assert CountPersistence.list_location_counts(project.id, [], @unclosed) == [
             %{list_id: nil, parent_list_id: nil, visible_count: 0, eligible_count: 0}
           ]
  end
end
