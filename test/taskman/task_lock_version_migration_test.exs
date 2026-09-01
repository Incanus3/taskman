defmodule Taskman.TaskLockVersionMigrationTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks

  test "new Tasks start at lock version one and successful updates increment it" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Versioned"})

    assert Map.get(task, :lock_version) == 1

    assert {:ok, updated} = Tasks.update_task(project, task, %{title: "Updated"})
    assert updated.lock_version == 2
  end

  test "the database requires a lock version with a default of one" do
    assert %{rows: [["NO", "1"]]} =
             Ecto.Adapters.SQL.query!(
               Repo,
               """
               SELECT is_nullable, column_default
               FROM information_schema.columns
               WHERE table_schema = current_schema()
                 AND table_name = 'tasks'
                 AND column_name = 'lock_version'
               """
             )
  end

  test "change_task ignores a caller supplied lock version" do
    project = project_fixture(%{})

    changeset = Tasks.change_task(project, %{title: "No external version", lock_version: 999})

    assert Ecto.Changeset.get_field(changeset, :lock_version) == 1
    refute Map.has_key?(changeset.changes, :lock_version)
  end
end
