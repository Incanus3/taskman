defmodule Taskman.Tasks.MutationsTest do
  use Taskman.DataCase, async: false

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias Taskman.Tasks.Task

  test "persisted status changes acquire the graph lock before reloading the Task" do
    project = project_fixture(%{})

    for status <- Task.statuses(), status != :pending do
      task = task_fixture(project, %{title: "Change to #{status}"})

      {result, queries} =
        capture_queries(fn -> Tasks.update_task(project, task, %{status: status}) end)

      assert {:ok, updated} = result
      assert updated.status == status

      graph_lock_positions = query_positions(queries, "pg_advisory_xact_lock")
      task_read_positions = query_positions(queries, ~s(FROM "tasks"))

      assert length(graph_lock_positions) == 1
      assert length(task_read_positions) >= 1
      assert hd(graph_lock_positions) < hd(task_read_positions)
      assert Enum.any?(queries, &String.contains?(&1, "begin"))
      assert Enum.any?(queries, &String.contains?(&1, "commit"))
    end
  end

  test "ordinary edits and parentless creation do not acquire the graph lock" do
    project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Destination"})
    task = task_fixture(project, %{title: "Ordinary"})

    {create_result, create_queries} =
      capture_queries(fn -> Tasks.create_task(project, %{title: "No parent"}) end)

    assert {:ok, _created} = create_result
    refute Enum.any?(create_queries, &String.contains?(&1, "pg_advisory_xact_lock"))

    {update_result, update_queries} =
      capture_queries(fn ->
        Tasks.update_task(project, task, %{
          title: "Renamed",
          description: "Details",
          priority: :high,
          due_at: ~N[2026-10-01 12:00:00],
          status: :pending
        })
      end)

    assert {:ok, updated} = update_result
    refute Enum.any?(update_queries, &String.contains?(&1, "pg_advisory_xact_lock"))

    {move_result, move_queries} =
      capture_queries(fn -> Tasks.move_task(project, updated, destination) end)

    assert {:ok, _moved} = move_result
    refute Enum.any?(move_queries, &String.contains?(&1, "pg_advisory_xact_lock"))
  end

  test "an unchanged explicit status uses no graph lock or version bump" do
    project = project_fixture(%{})
    task = task_fixture(project, %{status: :done})

    {result, queries} =
      capture_queries(fn -> Tasks.update_task(project, task, %{status: :done}) end)

    assert {:ok, current} = result
    assert current.status == :done
    assert current.lock_version == task.lock_version
    refute Enum.any?(queries, &String.contains?(&1, "pg_advisory_xact_lock"))
  end

  defp query_positions(queries, pattern) do
    queries
    |> Enum.with_index()
    |> Enum.filter(fn {query, _index} -> String.contains?(query, pattern) end)
    |> Enum.map(&elem(&1, 1))
  end

  defp capture_queries(fun) do
    test_pid = self()
    handler_id = {__MODULE__, make_ref()}

    :ok =
      :telemetry.attach(
        handler_id,
        [:taskman, :repo, :query],
        fn _event, _measurements, %{query: query}, _config ->
          send(test_pid, {:captured_query, query})
        end,
        nil
      )

    try do
      result = fun.()
      {result, drain_captured_queries([])}
    after
      :telemetry.detach(handler_id)
    end
  end

  defp drain_captured_queries(queries) do
    receive do
      {:captured_query, query} -> drain_captured_queries([query | queries])
    after
      0 -> Enum.reverse(queries)
    end
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
