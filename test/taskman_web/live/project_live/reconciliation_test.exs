defmodule TaskmanWeb.ProjectLive.ReconciliationTest do
  use Taskman.DataCase, async: true

  import Taskman.ProjectsFixtures
  import Taskman.ListsFixtures
  import Taskman.TasksFixtures

  alias Taskman.ChangeNotifications.Event
  alias Taskman.Tasks
  alias Taskman.Lists
  alias Taskman.Projects
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

    {:noreply, socket} =
      ProjectLive.handle_event("search_task_parents", %{"parent_query" => "Candidate"}, socket)

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
          if self() == owner and String.contains?(metadata.query, "strpos(lower(") do
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

  test "detail route loads relationships and a matching local invalidation refetches foreign changes" do
    local_project = project_fixture(%{name: "Local"})
    foreign_project = project_fixture(%{name: "Foreign"})
    selected = task_fixture(local_project)
    foreign = task_fixture(foreign_project, %{title: "Before"})
    assert {:ok, _edge} = Tasks.add_block(local_project, selected, foreign)

    socket = open_detail(local_project, selected)
    assert [%{id: foreign_id, title: "Before"}] = socket.assigns.editing.related_tasks.blocks
    assert foreign_id == foreign.id

    assert {:ok, _updated} = Tasks.update_task(foreign_project, foreign, %{title: "After"})

    event = relationship_event(local_project.id, [selected.id])
    {:noreply, refreshed} = Reconciliation.handle_info(event, socket)

    assert refreshed.assigns.editing.selected_task.id == selected.id

    assert [%{id: ^foreign_id, title: "After", project_id: foreign_project_id}] =
             refreshed.assigns.editing.related_tasks.blocks

    assert foreign_project_id == foreign_project.id
  end

  test "relationship invalidations for another local Task do not refetch open detail" do
    project = project_fixture(%{})
    selected = task_fixture(project)
    linked = task_fixture(project)
    other = task_fixture(project)
    assert {:ok, _edge} = Tasks.add_block(project, selected, linked)
    socket = open_detail(project, selected)
    assert [_] = socket.assigns.editing.related_tasks.blocks

    assert {:ok, _edge} = Tasks.remove_block(project, selected, linked)

    {:noreply, unchanged} =
      Reconciliation.handle_info(relationship_event(project.id, [other.id]), socket)

    assert unchanged.assigns.editing.related_tasks == socket.assigns.editing.related_tasks

    {:noreply, refreshed} =
      Reconciliation.handle_info(relationship_event(project.id, [linked.id, selected.id]), socket)

    assert refreshed.assigns.editing.related_tasks.blocks == []
  end

  test "Project and ancestor List name events refresh open cross-Project relationship paths" do
    local_project = project_fixture(%{name: "Local"})
    foreign_project = project_fixture(%{name: "Before Project"})
    parent_list = list_fixture(foreign_project, nil, %{name: "Before Parent"})
    child_list = list_fixture(foreign_project, parent_list, %{name: "Child"})
    selected = task_fixture(local_project)
    foreign = task_fixture(foreign_project, child_list, %{title: "Linked"})
    assert {:ok, _edge} = Tasks.add_block(local_project, selected, foreign)
    socket = open_detail(local_project, selected)

    assert [%{project_name: "Before Project", location: %{path: ["Before Parent", "Child"]}}] =
             socket.assigns.editing.related_tasks.blocks

    assert {:ok, renamed_project} =
             Projects.update_project(foreign_project, %{name: "After Project"})

    project_event = %Event{
      entity: :project,
      operation: :updated,
      project_id: foreign_project.id,
      entity_id: foreign_project.id,
      fields: [:name]
    }

    {:noreply, socket} = Reconciliation.handle_info(project_event, socket)

    assert [%{project_name: "After Project"}] = socket.assigns.editing.related_tasks.blocks

    assert {:ok, renamed_list} =
             Lists.rename_list(renamed_project, parent_list, %{name: "After Parent"})

    list_event = %Event{
      entity: :list,
      operation: :updated,
      project_id: foreign_project.id,
      entity_id: renamed_list.id,
      fields: [:name]
    }

    {:noreply, refreshed} = Reconciliation.handle_info(list_event, socket)

    assert [%{location: %{path: ["After Parent", "Child"]}}] =
             refreshed.assigns.editing.related_tasks.blocks
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

  defp relationship_event(project_id, task_ids) do
    %Event{
      entity: :relationship,
      operation: :invalidated,
      project_id: project_id,
      entity_id: hd(task_ids),
      task_ids: task_ids,
      fields: []
    }
  end
end
