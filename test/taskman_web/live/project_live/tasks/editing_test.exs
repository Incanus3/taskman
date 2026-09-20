defmodule TaskmanWeb.ProjectLive.Tasks.EditingTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks
  alias TaskmanWeb.ProjectLive
  alias TaskmanWeb.ProjectLive.Tasks.{Autosave, Creation, Editing, Hierarchy}
  alias TaskmanWeb.ProjectLive.Tasks.Editing.State

  test "clearing detail removes its Task and hierarchy while preserving timer sequence" do
    project = project_fixture(%{})
    task = task_fixture(project)
    {:ok, hierarchy} = Tasks.get_task_hierarchy(project, task)
    autosave = Autosave.load(%{Autosave.empty() | sequence: 7}, task, saved?: false)

    state = State.open(State.empty(), task, autosave, hierarchy)
    assert state.selected_task == task
    assert state.detail_open?
    assert state.autosave.baseline.id == task.id
    refute state.autosave.saved?
    assert state.autosave.save_state == :idle
    assert state.hierarchy.hierarchy.selected_task_id == task.id

    transient = State.clear_transient(state)
    assert transient.selected_task == nil
    assert transient.detail_open?
    assert transient.hierarchy == state.hierarchy
    assert transient.autosave == %{Autosave.empty() | sequence: 7}

    cleared = State.clear(state)
    assert cleared.selected_task == nil
    refute cleared.detail_open?
    assert cleared.autosave == %{Autosave.empty() | sequence: 7}
    assert cleared.hierarchy == Hierarchy.empty()
  end

  test "not-found state removes incompatible detail data" do
    project = project_fixture(%{})
    task = task_fixture(project)
    state = %{State.empty() | selected_task: task, detail_open?: true}

    state = State.not_found(state)
    assert state.not_found?
    assert state.selected_task == nil
    refute state.detail_open?
    assert state.autosave == Autosave.empty()
    assert state.hierarchy == Hierarchy.empty()
  end

  test "missing detail hierarchy clears creation populated by late validation" do
    project = project_fixture(%{})
    task = task_fixture(project)

    socket = %Phoenix.LiveView.Socket{
      private: %{live_temp: %{}, lifecycle: Phoenix.LiveView.Lifecycle.build([])}
    }

    {:ok, socket} = ProjectLive.mount(%{}, %{}, socket)
    socket = Phoenix.Component.assign(socket, :live_action, :show_task)

    {:noreply, socket} =
      ProjectLive.handle_params(
        %{"project_id" => Integer.to_string(project.id), "task_id" => Integer.to_string(task.id)},
        nil,
        socket
      )

    assert socket.assigns.editing.selected_task == task

    {:noreply, socket} =
      ProjectLive.handle_event(
        "validate_task",
        %{"task" => %{"title" => "Late creation draft"}},
        socket
      )

    assert socket.assigns.creation.form.params["title"] == "Late creation draft"
    assert socket.assigns.creation.enabled?

    Taskman.Repo.delete!(task)
    socket = Editing.reload_hierarchy(socket)

    assert socket.assigns.creation == Creation.State.empty()
    assert socket.assigns.editing.not_found?
    assert socket.assigns.editing.selected_task == nil
    refute socket.assigns.editing.detail_open?
    assert socket.assigns.editing.hierarchy == Hierarchy.empty()
  end

  test "restore identifies a Task that disappears during fresh reconstruction" do
    project = project_fixture(%{})
    task = task_fixture(project)

    socket = %Phoenix.LiveView.Socket{
      private: %{live_temp: %{}, lifecycle: Phoenix.LiveView.Lifecycle.build([])}
    }

    {:ok, socket} = ProjectLive.mount(%{}, %{}, socket)
    socket = Phoenix.Component.assign(socket, :live_action, :show_task)

    {:noreply, socket} =
      ProjectLive.handle_params(
        %{"project_id" => "#{project.id}", "task_id" => "#{task.id}"},
        nil,
        socket
      )

    captured_editing = socket.assigns.editing
    captured_picker = socket.assigns.task_parent_picker
    Taskman.Repo.delete!(task)
    cleared = Editing.clear(socket)

    assert {:error, :task_not_found, ^cleared} =
             Editing.restore(cleared, captured_editing, captured_picker)
  end
end
