defmodule Taskman.TasksTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.Task
  alias Taskman.Tasks.TaskWithLocation

  test "create_task/2 assigns ownership and product defaults" do
    project = project_fixture(%{})

    assert {:ok, task} = Tasks.create_task(project, %{title: "First task"})
    assert task.project_id == project.id
    assert task.title == "First task"
    assert task.description == ""
    assert task.status == :pending
    assert task.priority == :none
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

  test "change_task/2 builds a valid Project-owned creation changeset" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})

    changeset =
      Tasks.change_task(project, %{
        title: "  Planned task  ",
        description: "Creation details",
        status: :in_progress,
        priority: :high,
        due_at: ~N[2026-08-03 16:00:00],
        project_id: other_project.id
      })

    assert changeset.valid?
    assert Ecto.Changeset.get_field(changeset, :project_id) == project.id
    assert Ecto.Changeset.get_field(changeset, :title) == "Planned task"
    assert Ecto.Changeset.get_field(changeset, :description) == "Creation details"
    assert Ecto.Changeset.get_field(changeset, :status) == :in_progress
    assert Ecto.Changeset.get_field(changeset, :priority) == :high
    assert Ecto.Changeset.get_field(changeset, :due_at) == ~N[2026-08-03 16:00:00]
  end

  test "list_tasks_for_project/1 returns only that Project's tasks in stable order" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    {:ok, first} = Tasks.create_task(project, %{title: "First"})
    {:ok, second} = Tasks.create_task(project, %{title: "Second"})
    {:ok, _other} = Tasks.create_task(other_project, %{title: "Other"})

    assert Tasks.list_tasks_for_project(project) == [first, second]
  end

  test "list_tasks_for_location/3 returns direct Project Tasks with empty paths" do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    direct = task_fixture(project, %{title: "Direct"})
    listed_task = task_fixture(project, root, %{title: "Listed"})

    assert {:ok, direct_listed} =
             Tasks.list_tasks_for_location(project, nil, include_descendants: false)

    assert Enum.map(direct_listed, & &1.task.id) == [direct.id]
    assert [%TaskWithLocation{task: ^direct, location_path: []}] = direct_listed

    assert {:ok, descendants} =
             Tasks.list_tasks_for_location(project, nil, include_descendants: true)

    assert Enum.map(descendants, & &1.task.id) == [direct.id, listed_task.id]
    assert Enum.at(descendants, 1).location_path == [root]
  end

  test "list_tasks_for_location/3 returns direct and descendant List Tasks with full paths" do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    child = list_fixture(project, root, %{name: "Launch"})
    leaf = list_fixture(project, child, %{name: "Copy"})
    direct = task_fixture(project, root, %{title: "Root task"})
    nested = task_fixture(project, leaf, %{title: "Nested task"})

    _unrelated =
      task_fixture(project, list_fixture(project, nil, %{name: "Other"}), %{title: "Other task"})

    assert {:ok, direct_only} =
             Tasks.list_tasks_for_location(project, root, include_descendants: false)

    assert Enum.map(direct_only, & &1.task.id) == [direct.id]
    assert Enum.map(hd(direct_only).location_path, & &1.name) == ["Planning"]

    assert {:ok, descendants} =
             Tasks.list_tasks_for_location(project, root, include_descendants: true)

    assert Enum.map(descendants, & &1.task.id) == [direct.id, nested.id]

    assert Enum.map(Enum.at(descendants, 1).location_path, & &1.name) == [
             "Planning",
             "Launch",
             "Copy"
           ]
  end

  test "list_tasks_for_location/3 keeps Project descendant results in global Task order" do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    direct = task_fixture(project, %{title: "Direct"})
    listed = task_fixture(project, root, %{title: "Listed"})

    timestamp = ~U[2026-08-03 16:00:00.000000Z]
    Repo.update_all(Task, set: [inserted_at: timestamp])

    assert {:ok, descendants} =
             Tasks.list_tasks_for_location(project, nil, include_descendants: true)

    assert Enum.map(descendants, & &1.task.id) == Enum.sort([direct.id, listed.id])
  end

  test "list_tasks_for_location/3 rejects a foreign List before listing" do
    project = project_fixture(%{})
    foreign_list = list_fixture(project_fixture(%{}), nil, %{name: "Foreign"})

    assert {:error, :not_found} =
             Tasks.list_tasks_for_location(project, foreign_list, include_descendants: true)
  end

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
