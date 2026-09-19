defmodule TaskmanWeb.ProjectLive.WorkspaceTest do
  use Taskman.DataCase, async: true

  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures

  alias TaskmanWeb.ProjectLive.ListEdit
  alias TaskmanWeb.ProjectLive.Workspace
  alias TaskmanWeb.ProjectLive.Workspace.State
  alias Taskman.ChangeNotifications.Event

  test "location transitions keep found and not-found state mutually consistent" do
    project = project_fixture(%{})
    task_list = list_fixture(project)
    state = State.new(:project_form)

    selected = State.select_location(state, project, task_list, true, [task_list])
    assert selected.selected_project == project
    assert selected.selected_list == task_list
    assert selected.include_children?
    refute selected.project_not_found?
    refute selected.location_not_found?

    missing = State.location_not_found(selected, project)
    assert missing.selected_project == project
    assert missing.selected_list == nil
    assert missing.location_path == []
    assert missing.location_not_found?
    refute missing.project_not_found?

    assert State.project_not_found(missing).project_not_found?
  end

  test "node and List-edit transitions remain inside workspace state" do
    project = project_fixture(%{})
    state = State.new(:project_form)
    list_edit = ListEdit.open_new(project, nil)

    state = state |> State.toggle_node({:project, project.id}) |> State.put_list_edit(list_edit)
    assert MapSet.member?(state.expanded_node_ids, {:project, project.id})
    assert state.list_edit == list_edit
    assert State.clear_list_edit(state).list_edit == ListEdit.empty()
  end

  test "selected-location reconciliation returns canonical Lists without touching downstream state" do
    project = project_fixture(%{})
    task_list = list_fixture(project)
    state = State.select_location(State.new(:project_form), project, task_list, true, [task_list])
    socket = reconciliation_socket(state)
    {:ok, renamed} = Taskman.Lists.rename_list(project, task_list, %{name: "Renamed"})

    assert {updated, {:location_changed, [^renamed]}} =
             Workspace.reconcile(socket, %Event{
               entity: :list,
               operation: :updated,
               project_id: project.id,
               entity_id: 1,
               fields: [:name]
             })

    assert updated.assigns.workspace.selected_list == renamed
    assert updated.assigns.workspace.location_path == [renamed]
    assert updated.assigns.workspace.include_children?
    assert_downstream_unchanged(socket, updated)
  end

  test "missing selected location returns a missing outcome and preserves downstream drafts" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    unavailable_list = list_fixture(other_project)

    state =
      State.select_location(State.new(:project_form), project, unavailable_list, true, [
        unavailable_list
      ])

    socket = reconciliation_socket(state)

    assert {updated, {:location_missing, []}} =
             Workspace.reconcile(socket, %Event{
               entity: :list,
               operation: :updated,
               project_id: project.id,
               entity_id: 1,
               fields: [:name]
             })

    assert updated.assigns.workspace.location_not_found?
    assert updated.assigns.workspace.selected_project == project
    assert updated.assigns.workspace.selected_list == nil
    assert updated.assigns.workspace.location_path == []
    assert_downstream_unchanged(socket, updated)
  end

  test "Project and unrelated List notifications require no selected-location refresh" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    state = State.select_location(State.new(:project_form), project, nil, false, [])
    socket = reconciliation_socket(state)

    for event <- [
          %Event{
            entity: :project,
            operation: :created,
            project_id: other_project.id,
            entity_id: other_project.id,
            fields: [:name]
          },
          %Event{
            entity: :list,
            operation: :updated,
            project_id: other_project.id,
            entity_id: 1,
            fields: [:name]
          }
        ] do
      assert {updated, :unchanged} = Workspace.reconcile(socket, event)
      assert updated.assigns.workspace.selected_project == project
      assert_downstream_unchanged(socket, updated)
    end
  end

  defp reconciliation_socket(state) do
    %Phoenix.LiveView.Socket{
      private: %{live_temp: %{}, lifecycle: Phoenix.LiveView.Lifecycle.build([])}
    }
    |> Phoenix.Component.assign(:workspace, state)
    |> Phoenix.Component.assign(:creation, :creation_draft)
    |> Phoenix.Component.assign(:listing, :listing_state)
    |> Phoenix.Component.assign(:editing, :editing_draft)
    |> Phoenix.Component.assign(:task_parent_picker, :parent_draft)
    |> Phoenix.Component.assign(:task_move, :movement_draft)
    |> Phoenix.LiveView.stream_configure(:navigation_nodes, dom_id: & &1.dom_id)
  end

  defp assert_downstream_unchanged(before, after_socket) do
    for key <- [:creation, :listing, :editing, :task_parent_picker, :task_move] do
      assert Map.fetch!(after_socket.assigns, key) == Map.fetch!(before.assigns, key)
    end
  end
end
