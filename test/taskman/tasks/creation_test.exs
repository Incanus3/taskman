defmodule Taskman.Tasks.CreationTest do
  use Taskman.DataCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

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

  test "create_task/4 creates a Task with a same-Project parent" do
    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})

    assert {:ok, child} =
             Tasks.create_task(project, nil, %{title: "Child"}, parent: parent)

    assert child.parent_task_id == parent.id
  end

  test "create_task/3 creates a direct Project Task and ignores a supplied list ID" do
    project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})

    assert {:ok, task} =
             Tasks.create_task(project, nil, %{
               title: "Direct task",
               list_id: list.id
             })

    assert task.project_id == project.id
    assert task.list_id == nil
  end

  test "create_task/3 creates a List-owned Task and ignores supplied ownership IDs" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})
    other_list = list_fixture(other_project, nil, %{name: "Other"})

    assert {:ok, task} =
             Tasks.create_task(project, list, %{
               title: "List task",
               project_id: other_project.id,
               list_id: other_list.id
             })

    assert task.project_id == project.id
    assert task.list_id == list.id
  end

  test "create_task/3 rejects a List owned by another Project" do
    project = project_fixture(%{})
    foreign_list = list_fixture(project_fixture(%{}), nil, %{name: "Foreign"})

    assert {:error, :not_found} = Tasks.create_task(project, foreign_list, %{title: "No leak"})
  end

  test "create_task/2 persists an explicitly empty description as an empty string" do
    project = project_fixture(%{})

    assert {:ok, task} =
             Tasks.create_task(project, %{title: "Empty description", description: ""})

    assert task.description == ""
  end

  test "the database defaults an omitted Task description to an empty string" do
    project = project_fixture(%{})

    result =
      Ecto.Adapters.SQL.query!(
        Repo,
        """
        INSERT INTO tasks (project_id, title, inserted_at, updated_at)
        VALUES ($1, $2, NOW(), NOW())
        RETURNING id, description
        """,
        [project.id, "Database default"]
      )

    assert [[task_id, ""]] = result.rows

    assert %{rows: [[""]]} =
             Ecto.Adapters.SQL.query!(
               Repo,
               "SELECT description FROM tasks WHERE id = $1",
               [task_id]
             )
  end

  test "the database rejects an explicitly null Task description without aborting the sandbox" do
    project = project_fixture(%{})

    error =
      assert_raise Postgrex.Error, fn ->
        Repo.transaction(
          fn ->
            Ecto.Adapters.SQL.query!(
              Repo,
              """
              INSERT INTO tasks (project_id, title, description, inserted_at, updated_at)
              VALUES ($1, $2, $3, NOW(), NOW())
              """,
              [project.id, "Rejected null", nil]
            )
          end,
          mode: :savepoint
        )
      end

    assert error.postgres.code == :not_null_violation

    assert %{rows: [[1]]} = Ecto.Adapters.SQL.query!(Repo, "SELECT 1")
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

  test "ordinary create and update attrs cannot change a Task's list ownership" do
    project = project_fixture(%{})
    list = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Owned"})

    assert {:ok, updated} =
             Tasks.update_task(project, task, %{title: "Still owned", list_id: list.id})

    assert updated.list_id == nil

    assert Ecto.Changeset.get_field(
             Tasks.change_task(project, %{title: "New", list_id: list.id}),
             :list_id
           ) == nil
  end
end
