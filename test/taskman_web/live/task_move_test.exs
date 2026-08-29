defmodule TaskmanWeb.TaskMoveTest do
  use Taskman.DataCase, async: true

  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks.TaskWithLocation
  alias TaskmanWeb.TaskMove

  test "empty and clear provide one inactive source of truth" do
    state = TaskMove.empty()

    refute TaskMove.active?(state)
    refute TaskMove.active_for?(state, 41, :row)
    assert TaskMove.current_destination(state) == nil
    assert state.query == ""
    assert state.destination == nil
    assert state.options == []
    refute state.options_open?
    assert state.error == nil

    assert TaskMove.clear(TaskMove.put_error(state, "failure")) == state
  end

  test "opens a row movement with cached location and tree-ordered destinations" do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    launch = list_fixture(project, planning, %{name: "Launch"})
    archive = list_fixture(project, nil, %{name: "Archive"})
    task = task_fixture(project, launch, %{title: "Move me"})

    state = TaskMove.open(TaskMove.empty(), project, task, :row)

    assert TaskMove.active?(state)
    assert TaskMove.active_for?(state, task.id, :row)
    refute TaskMove.active_for?(state, task.id, :detail)
    assert TaskMove.current_destination(state) == "list:#{launch.id}"

    assert %TaskWithLocation{task: ^task, location_path: [^planning, ^launch]} =
             state.active_task.task_with_location

    assert Enum.map(state.options, &{&1.value, &1.label, &1.current?}) == [
             {"project", "Project · #{project.name}", false},
             {"list:#{planning.id}", "Planning", false},
             {"list:#{launch.id}", "Planning / Launch", true},
             {"list:#{archive.id}", "Archive", false}
           ]
  end

  test "opens detail movement without caching a row" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Move me"})

    state = TaskMove.open(TaskMove.empty(), project, task, :detail)

    assert TaskMove.active_for?(state, task.id, :detail)
    assert state.active_task.task_with_location == nil
    assert TaskMove.current_destination(state) == "project"
  end

  test "opening another Task discards all prior transient state" do
    project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Planning"})
    first = task_fixture(project, %{title: "First"})
    second = task_fixture(project, destination, %{title: "Second"})

    state =
      TaskMove.empty()
      |> TaskMove.open(project, first, :row)
      |> TaskMove.open_destinations()
      |> TaskMove.put_error("failure")

    reopened = TaskMove.open(state, project, second, :detail)

    assert TaskMove.active_for?(reopened, second.id, :detail)
    assert reopened.query == ""
    assert reopened.destination == nil
    refute reopened.options_open?
    assert reopened.error == nil
  end

  test "selection labels the query, closes options, and clears an error" do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me"})

    state =
      TaskMove.empty()
      |> TaskMove.open(project, task, :row)
      |> TaskMove.open_destinations()
      |> TaskMove.put_error("failure")
      |> TaskMove.select_destination("list:#{planning.id}")

    assert state.destination == "list:#{planning.id}"
    assert state.query == "Planning"
    refute state.options_open?
    assert state.error == nil
  end

  test "changed search clears selection and filters complete paths case-insensitively" do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    launch = list_fixture(project, planning, %{name: "Launch"})
    task = task_fixture(project, %{title: "Move me"})

    selected =
      TaskMove.empty()
      |> TaskMove.open(project, task, :row)
      |> TaskMove.select_destination("list:#{planning.id}")

    assert {:ok, searched, ^task} = TaskMove.search(selected, project, "lAuNcH")
    assert searched.destination == nil
    assert searched.options_open?
    assert Enum.map(searched.options, & &1.value) == ["list:#{launch.id}"]
  end

  test "identical search keeps the selected destination" do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me"})

    selected =
      TaskMove.empty()
      |> TaskMove.open(project, task, :row)
      |> TaskMove.select_destination("list:#{planning.id}")

    assert {:ok, searched, ^task} = TaskMove.search(selected, project, "Planning")
    assert searched.destination == "list:#{planning.id}"
  end

  test "refresh rebuilds renamed paths and persisted current location" do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    launch = list_fixture(project, planning, %{name: "Launch"})
    task = task_fixture(project, launch, %{title: "Move me"})
    state = TaskMove.open(TaskMove.empty(), project, task, :row)

    assert {:ok, renamed_planning} =
             Taskman.Lists.rename_list(project, planning, %{name: "Roadmap"})

    assert {:ok, refreshed, refreshed_task} = TaskMove.refresh(state, project)
    assert refreshed_task.id == task.id

    assert Enum.any?(
             refreshed.options,
             &(&1.value == "list:#{launch.id}" and &1.label == "Roadmap / Launch")
           )

    assert refreshed.active_task.task_with_location.location_path == [renamed_planning, launch]
  end

  test "refresh observes an external Task move" do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me"})
    state = TaskMove.open(TaskMove.empty(), project, task, :row)
    assert {:ok, _moved} = Taskman.Tasks.move_task(project, task, planning)

    assert {:ok, refreshed, refreshed_task} = TaskMove.refresh(state, project)
    assert refreshed_task.list_id == planning.id
    assert TaskMove.current_destination(refreshed) == "list:#{planning.id}"
  end

  test "refresh clears state when the active Task is gone" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Move me"})
    state = TaskMove.open(TaskMove.empty(), project, task, :row)
    Taskman.Repo.delete!(task)

    assert {:error, cleared, :task_not_found} = TaskMove.refresh(state, project)
    assert cleared == TaskMove.empty()
  end

  test "submit moves to a List and clears movement state" do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me"})

    state =
      TaskMove.empty()
      |> TaskMove.open(project, task, :row)
      |> TaskMove.select_destination("list:#{planning.id}")

    assert {:ok, cleared, moved_task} = TaskMove.submit(state, project)
    assert cleared == TaskMove.empty()
    assert moved_task.list_id == planning.id
  end

  test "submit moves to the Project root" do
    project = project_fixture(%{})
    inbox = list_fixture(project, nil, %{name: "Inbox"})
    task = task_fixture(project, inbox, %{title: "Move me"})

    state =
      TaskMove.empty()
      |> TaskMove.open(project, task, :detail)
      |> TaskMove.select_destination("project")

    assert {:ok, cleared, moved_task} = TaskMove.submit(state, project)
    assert cleared == TaskMove.empty()
    assert moved_task.list_id == nil
  end

  test "submit retains a cached row and reports a deleted Task" do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me"})

    state =
      TaskMove.empty()
      |> TaskMove.open(project, task, :row)
      |> TaskMove.select_destination("list:#{planning.id}")

    cached_row = state.active_task.task_with_location
    Taskman.Repo.delete!(task)

    assert {:error, failed, :task_not_found} = TaskMove.submit(state, project)
    assert failed.active_task.task_with_location == cached_row
    assert failed.error == "This Task is no longer available."
  end

  test "submit rejects missing and foreign destinations" do
    project = project_fixture(%{})
    foreign_project = project_fixture(%{})
    foreign_list = list_fixture(foreign_project, nil, %{name: "Foreign"})
    task = task_fixture(project, %{title: "Move me"})

    state =
      TaskMove.empty()
      |> TaskMove.open(project, task, :row)
      |> TaskMove.select_destination("list:#{foreign_list.id}")

    assert {:error, failed, :destination_not_found} = TaskMove.submit(state, project)
    assert failed.error == "That destination is no longer available."
    assert Taskman.Tasks.get_task_for_project(project, task.id).list_id == nil
  end

  test "submit rejects a malformed List destination without moving the Task" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Move me"})

    state =
      TaskMove.empty()
      |> TaskMove.open(project, task, :row)
      |> TaskMove.select_destination("list:not-an-id")

    assert {:error, failed, :destination_not_found} = TaskMove.submit(state, project)
    assert failed.error == "That destination is no longer available."
    assert Taskman.Tasks.get_task_for_project(project, task.id).list_id == nil
  end

  test "submit rejects a nil destination without moving the Task" do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Move me"})

    state =
      TaskMove.empty()
      |> TaskMove.open(project, task, :row)
      |> Map.put(:destination, nil)

    assert {:error, failed, :destination_not_found} = TaskMove.submit(state, project)
    assert failed.error == "That destination is no longer available."
    assert Taskman.Tasks.get_task_for_project(project, task.id).list_id == nil
  end

  test "submit rejects a deleted same-Project List without moving the Task" do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me"})

    state =
      TaskMove.empty()
      |> TaskMove.open(project, task, :row)
      |> TaskMove.select_destination("list:#{planning.id}")

    Taskman.Repo.delete!(planning)

    assert {:error, failed, :destination_not_found} = TaskMove.submit(state, project)
    assert failed.error == "That destination is no longer available."
    assert Taskman.Tasks.get_task_for_project(project, task.id).list_id == nil
  end

  test "submit refreshes an externally changed destination before unchanged error" do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me"})

    state =
      TaskMove.empty()
      |> TaskMove.open(project, task, :row)
      |> TaskMove.select_destination("list:#{planning.id}")

    assert {:ok, _moved} = Taskman.Tasks.move_task(project, task, planning)

    assert {:error, failed, :unchanged_location} = TaskMove.submit(state, project)
    assert TaskMove.current_destination(failed) == "list:#{planning.id}"
    assert failed.error == "This Task is already in that location."
  end
end
