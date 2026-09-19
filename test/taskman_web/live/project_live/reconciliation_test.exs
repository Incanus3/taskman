defmodule TaskmanWeb.ProjectLive.ReconciliationTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.ChangeNotifications.Event
  alias Taskman.Tasks
  alias TaskmanWeb.ProjectLive
  alias TaskmanWeb.ProjectLive.Reconciliation
  alias TaskmanWeb.ProjectLive.Tasks.Editing

  test "a non-hierarchy notification preserves the picker when selected Task lookup fails" do
    project = project_fixture(%{})
    task = task_fixture(project)
    candidate = task_fixture(project, %{title: "Candidate before"})
    socket = open_detail(project, task)
    picker = socket.assigns.task_parent_picker

    Taskman.Repo.delete!(task)
    {:ok, _candidate} = Tasks.update_task(project, candidate, %{title: "Candidate after"})

    {:noreply, reconciled} = Reconciliation.handle_info(task_event(project, candidate), socket)

    assert reconciled.assigns.task_parent_picker == picker
    assert reconciled.assigns.editing == socket.assigns.editing
    assert {^socket, :unchanged} = Editing.reconcile(socket, task_event(project, candidate))
  end

  test "a successful selected Task lookup refreshes open parent candidates once" do
    project = project_fixture(%{})
    task = task_fixture(project)
    candidate = task_fixture(project, %{title: "Candidate before"})
    socket = open_detail(project, task)
    {:ok, candidate} = Tasks.update_task(project, candidate, %{title: "Candidate after"})
    {:ok, persisted_task} = Tasks.update_task(project, task, %{description: "Updated detail"})
    event = task_event(project, task)

    handler_id = {__MODULE__, make_ref()}
    owner = self()

    :ok =
      :telemetry.attach(
        handler_id,
        [:taskman, :repo, :query],
        fn _event, _measurements, metadata, owner ->
          if self() == owner and String.contains?(metadata.query, "ILIKE") do
            send(owner, :parent_candidate_query)
          end
        end,
        owner
      )

    on_exit(fn -> :telemetry.detach(handler_id) end)

    {:noreply, reconciled} = Reconciliation.handle_info(event, socket)

    assert_receive :parent_candidate_query
    refute_receive :parent_candidate_query, 0
    assert reconciled.assigns.editing.selected_task == persisted_task

    assert Enum.any?(reconciled.assigns.task_parent_picker.options, fn option ->
             option.task.id == candidate.id and option.task.title == "Candidate after"
           end)

    assert {edited, {:task_reconciled, ^persisted_task}} = Editing.reconcile(socket, event)
    assert edited.assigns.task_parent_picker == socket.assigns.task_parent_picker
    assert edited.assigns.editing.selected_task == persisted_task
  end

  test "detail reconciliation without a selected Task reports unchanged" do
    socket = %Phoenix.LiveView.Socket{assigns: %{__changed__: %{}}}
    assert {^socket, :unchanged} = Editing.reconcile(socket, nil)
  end

  defp open_detail(project, task) do
    socket = %Phoenix.LiveView.Socket{
      private: %{live_temp: %{}, lifecycle: Phoenix.LiveView.Lifecycle.build([])}
    }

    {:ok, socket} = ProjectLive.mount(%{}, %{}, socket)
    socket = Phoenix.Component.assign(socket, :live_action, :show_task)

    {:noreply, socket} =
      ProjectLive.handle_params(
        %{"project_id" => to_string(project.id), "task_id" => to_string(task.id)},
        nil,
        socket
      )

    {:noreply, socket} = ProjectLive.handle_event("open_task_parent_options", %{}, socket)
    socket
  end

  defp task_event(project, task) do
    %Event{
      entity: :task,
      operation: :updated,
      project_id: project.id,
      entity_id: task.id,
      lock_version: task.lock_version,
      fields: [:description]
    }
  end
end
