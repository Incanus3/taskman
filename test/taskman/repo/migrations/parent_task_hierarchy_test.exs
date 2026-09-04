defmodule Taskman.Repo.Migrations.ParentTaskHierarchyTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.Task

  test "parent_task_id is nullable" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Root"})

    assert %{rows: [[nil]]} =
             Ecto.Adapters.SQL.query!(Repo, "SELECT parent_task_id FROM tasks WHERE id = $1", [
               task.id
             ])
  end

  test "database enforces same-Project non-self parentage" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    task = task_fixture(project, %{})
    parent = task_fixture(project, %{})
    foreign = task_fixture(other_project, %{})

    assert {:ok, child} = Tasks.update_task(project, task, %{}, parent: parent)
    assert child.parent_task_id == parent.id

    foreign_changeset =
      task
      |> Task.changeset(%{})
      |> Ecto.Changeset.put_change(:parent_task_id, foreign.id)

    assert {:error, foreign_changeset} = Repo.update(foreign_changeset)
    assert "does not exist" in errors_on(foreign_changeset).parent_task_id

    self_changeset =
      task
      |> Task.changeset(%{})
      |> Ecto.Changeset.put_change(:parent_task_id, task.id)

    assert {:error, self_changeset} = Repo.update(self_changeset)
    assert "cannot be its own parent" in errors_on(self_changeset).parent_task_id
  end

  test "database provides the named hierarchy indexes and NO ACTION parent deletion" do
    index_names =
      Ecto.Adapters.SQL.query!(
        Repo,
        """
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = current_schema()
          AND tablename = 'tasks'
          AND indexname IN ('tasks_id_project_id_index', 'tasks_project_id_parent_task_id_index')
        ORDER BY indexname
        """
      )

    assert index_names.rows == [
             ["tasks_id_project_id_index"],
             ["tasks_project_id_parent_task_id_index"]
           ]

    assert %{rows: [["a"]]} =
             Ecto.Adapters.SQL.query!(
               Repo,
               "SELECT confdeltype::text FROM pg_constraint WHERE conname = $1",
               ["tasks_parent_task_id_project_id_fkey"]
             )

    project = project_fixture(%{})
    parent = task_fixture(project, %{title: "Parent"})
    child = task_fixture(project, %{title: "Child"}, parent: parent)

    error =
      assert_raise Postgrex.Error, fn ->
        Repo.transaction(
          fn ->
            Ecto.Adapters.SQL.query!(Repo, "DELETE FROM tasks WHERE id = $1", [parent.id])
          end,
          mode: :savepoint
        )
      end

    assert error.postgres.code == :foreign_key_violation
    assert error.postgres.constraint == "tasks_parent_task_id_project_id_fkey"
    assert child.parent_task_id == parent.id
  end
end
