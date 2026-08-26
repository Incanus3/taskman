defmodule Taskman.ListsAndTaskLocationsMigrationTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures
  import Taskman.ListsFixtures

  test "existing Tasks remain direct Project Tasks after the migration" do
    project = project_fixture(%{})
    task = task_fixture(project)

    assert %{rows: [[nil]]} =
             Ecto.Adapters.SQL.query!(Repo, "SELECT list_id FROM tasks WHERE id = $1", [task.id])
  end

  test "the database rejects a Task List from another Project" do
    project = project_fixture(%{})
    foreign_list = list_fixture(project_fixture(%{}), nil, %{name: "Foreign"})

    error =
      assert_raise Postgrex.Error, fn ->
        Repo.transaction(
          fn ->
            Ecto.Adapters.SQL.query!(
              Repo,
              """
              INSERT INTO tasks (project_id, list_id, title, inserted_at, updated_at)
              VALUES ($1, $2, $3, NOW(), NOW())
              """,
              [project.id, foreign_list.id, "Cross-project task"]
            )
          end,
          mode: :savepoint
        )
      end

    assert error.postgres.code == :foreign_key_violation
    assert %{rows: [[1]]} = Ecto.Adapters.SQL.query!(Repo, "SELECT 1")
  end

  test "the database rejects a parent List from another Project" do
    project = project_fixture(%{})
    other_parent = list_fixture(project_fixture(%{}), nil, %{name: "Other parent"})

    error =
      assert_raise Postgrex.Error, fn ->
        Repo.transaction(
          fn ->
            Ecto.Adapters.SQL.query!(
              Repo,
              """
              INSERT INTO lists (project_id, parent_list_id, name, inserted_at, updated_at)
              VALUES ($1, $2, $3, NOW(), NOW())
              """,
              [project.id, other_parent.id, "Cross-project child"]
            )
          end,
          mode: :savepoint
        )
      end

    assert error.postgres.code == :foreign_key_violation
    assert %{rows: [[1]]} = Ecto.Adapters.SQL.query!(Repo, "SELECT 1")
  end

  test "the database rejects duplicate root List names case-insensitively" do
    project = project_fixture(%{})
    _existing = list_fixture(project, nil, %{name: "Planning"})

    error =
      assert_raise Postgrex.Error, fn ->
        Repo.transaction(
          fn ->
            Ecto.Adapters.SQL.query!(
              Repo,
              """
              INSERT INTO lists (project_id, parent_list_id, name, inserted_at, updated_at)
              VALUES ($1, NULL, $2, NOW(), NOW())
              """,
              [project.id, "planning"]
            )
          end,
          mode: :savepoint
        )
      end

    assert error.postgres.code == :unique_violation
    assert %{rows: [[1]]} = Ecto.Adapters.SQL.query!(Repo, "SELECT 1")
  end

  test "the database rejects duplicate nested sibling names case-insensitively" do
    project = project_fixture(%{})
    parent = list_fixture(project, nil, %{name: "Planning"})
    _existing = list_fixture(project, parent, %{name: "Launch"})

    error =
      assert_raise Postgrex.Error, fn ->
        Repo.transaction(
          fn ->
            Ecto.Adapters.SQL.query!(
              Repo,
              """
              INSERT INTO lists (project_id, parent_list_id, name, inserted_at, updated_at)
              VALUES ($1, $2, $3, NOW(), NOW())
              """,
              [project.id, parent.id, "launch"]
            )
          end,
          mode: :savepoint
        )
      end

    assert error.postgres.code == :unique_violation
    assert %{rows: [[1]]} = Ecto.Adapters.SQL.query!(Repo, "SELECT 1")
  end
end
