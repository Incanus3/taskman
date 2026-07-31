defmodule Taskman.TasksTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.Task

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

  test "list_tasks_for_project/1 returns only that Project's tasks in stable order" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    {:ok, first} = Tasks.create_task(project, %{title: "First"})
    {:ok, second} = Tasks.create_task(project, %{title: "Second"})
    {:ok, _other} = Tasks.create_task(other_project, %{title: "Other"})

    assert Tasks.list_tasks_for_project(project) == [first, second]
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
