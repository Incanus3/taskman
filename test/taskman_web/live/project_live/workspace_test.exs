defmodule TaskmanWeb.ProjectLive.WorkspaceTest do
  use Taskman.DataCase, async: true

  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures

  import Taskman.TasksFixtures

  alias TaskmanWeb.ProjectLive.Locations
  alias TaskmanWeb.ProjectLive.ListEdit
  alias TaskmanWeb.ProjectLive.Workspace
  alias TaskmanWeb.ProjectLive.Workspace.State
  alias Taskman.ChangeNotifications.Event

  test "location transitions keep found and not-found state mutually consistent" do
    project = project_fixture(%{})
    task_list = list_fixture(project)
    state = State.new()

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
    state = State.new()
    list_edit = ListEdit.open_new(project, nil)

    state = state |> State.toggle_node({:project, project.id}) |> State.put_list_edit(list_edit)
    assert MapSet.member?(state.expanded_node_ids, {:project, project.id})
    assert state.list_edit == list_edit
    assert State.clear_list_edit(state).list_edit == ListEdit.empty()
  end

  test "selected-location reconciliation returns canonical Lists without touching downstream state" do
    project = project_fixture(%{})
    task_list = list_fixture(project)
    state = State.select_location(State.new(), project, task_list, true, [task_list])
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
      State.select_location(State.new(), project, unavailable_list, true, [
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
    state = State.select_location(State.new(), project, nil, false, [])
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

  test "navigation and paths reuse one List snapshot and availability bootstrap" do
    project = project_fixture(%{})
    parent = list_fixture(project)
    child = list_fixture(project, parent)
    task_fixture(project, child, %{status: :not_planned})

    socket =
      reconciliation_socket(State.select_location(State.new(), project, parent, false, [parent]))

    watch_location_queries()

    socket = Workspace.ensure_locations(socket, project)
    assert_receive :location_list_query
    refute_receive :location_list_query, 0
    assert_receive :availability_query
    refute_receive :availability_query, 0

    for _ <- 1..3 do
      refreshed = socket |> Workspace.ensure_locations(project) |> Workspace.refresh()
      assert Workspace.lists(refreshed) == [parent, child]
      assert Workspace.location_path(refreshed, project, child) == [parent, child]

      assert Locations.available(
               refreshed.assigns.workspace.locations,
               project.id,
               parent.id,
               :subtree
             ) == [:icebox, :pending, :in_progress, :in_review, :done, :not_planned, :will_not_do]
    end

    refute_receive :location_list_query, 0
    refute_receive :availability_query, 0
  end

  test "List reconciliation updates the owner with one authoritative snapshot and no Task discovery" do
    project = project_fixture(%{})
    task_list = list_fixture(project)

    socket =
      reconciliation_socket(
        State.select_location(State.new(), project, task_list, false, [task_list])
      )

    socket = Workspace.ensure_locations(socket, project)
    {:ok, renamed} = Taskman.Lists.rename_list(project, task_list, %{name: "Current"})
    watch_location_queries()

    {updated, {:location_changed, [^renamed]}} =
      Workspace.reconcile(socket, %Event{
        entity: :list,
        operation: :updated,
        project_id: project.id,
        entity_id: task_list.id,
        fields: [:name]
      })

    assert Workspace.lists(updated) == [renamed]
    assert_receive :location_list_query
    refute_receive :location_list_query, 0
    refute_receive :availability_query, 0
  end

  test "ordinary creation location validation reads the owner without another List query" do
    project = project_fixture(%{})
    task_list = list_fixture(project)

    socket =
      reconciliation_socket(
        State.select_location(State.new(), project, task_list, false, [task_list])
      )

    socket = Workspace.ensure_locations(socket, project)
    watch_location_queries()

    socket =
      Phoenix.Component.assign(
        socket,
        :creation,
        TaskmanWeb.ProjectLive.Tasks.Creation.State.empty()
      )
      |> Phoenix.Component.assign(:description, %TaskmanWeb.ProjectLive.Tasks.Description.State{})

    socket = TaskmanWeb.ProjectLive.Tasks.Creation.apply_route(socket, %{})

    {:noreply, validated} =
      TaskmanWeb.ProjectLive.Tasks.Creation.handle_event(
        "validate_task",
        %{"task" => %{"title" => "Draft"}, "location" => "list:#{task_list.id}"},
        socket
      )

    assert validated.assigns.creation.enabled?
    assert validated.assigns.creation.location == "list:#{task_list.id}"

    assert {"List #{task_list.name}", "list:#{task_list.id}"} in validated.assigns.creation.location_options

    refute_receive :location_list_query, 0
    refute_receive :availability_query, 0
  end

  test "creation parent authority reconciles an unknown owning List before preparing options" do
    project = project_fixture(%{})
    task_list = list_fixture(project)

    socket =
      reconciliation_socket(
        State.select_location(State.new(), project, task_list, false, [task_list])
      )

    socket =
      socket
      |> Workspace.ensure_locations(project)
      |> Phoenix.Component.assign(:creation, TaskmanWeb.ProjectLive.Tasks.Creation.State.empty())
      |> Phoenix.Component.assign(:description, %TaskmanWeb.ProjectLive.Tasks.Description.State{})

    child = list_fixture(project, task_list)
    parent_task = task_fixture(project, child, %{})
    watch_location_queries()

    socket =
      TaskmanWeb.ProjectLive.Tasks.Creation.apply_route(socket, %{
        "parent_task_id" => to_string(parent_task.id)
      })

    assert socket.assigns.creation.location == "list:#{child.id}"
    assert socket.assigns.creation.location_error == nil
    assert {"List #{child.name}", "list:#{child.id}"} in socket.assigns.creation.location_options
    assert Workspace.lists(socket) == [task_list, child]
    assert_receive :location_list_query
    refute_receive :location_list_query, 0
    refute_receive :availability_query, 0
  end

  test "Project return merges a fresh snapshot while ordinary same-Project routes reuse it" do
    project = project_fixture(%{})
    other_project = project_fixture(%{})
    parent = list_fixture(project)
    child = list_fixture(project, parent)
    task = task_fixture(project, child, %{})
    socket = navigation_socket()
    socket = Workspace.assign_location(socket, project, parent, false, false, false, [])
    watch_location_queries()

    socket = Workspace.assign_location(socket, project, child, false, false, true, [])
    assert socket.assigns.workspace.location_path == [parent, child]
    refute_receive :location_list_query, 0
    refute_receive :availability_query, 0

    socket = Workspace.assign_location(socket, other_project, nil, false, false, false, [])
    assert_receive :location_list_query
    assert_receive :availability_query

    {:ok, _} =
      Taskman.Tasks.update_task(project, task, %{status: :not_planned},
        actor: Taskman.AccountsFixtures.user_fixture()
      )

    socket = Workspace.assign_location(socket, project, parent, false, false, false, [])
    assert_receive :location_list_query
    assert_receive :availability_query
    refute_receive :location_list_query, 0
    refute_receive :availability_query, 0

    assert :not_planned in Locations.available(
             socket.assigns.workspace.locations,
             project.id,
             parent.id,
             :subtree
           )

    refute :not_planned in Locations.available(
             socket.assigns.workspace.locations,
             other_project.id,
             nil,
             :subtree
           )
  end

  test "route authority reconciles previously unknown and changed List metadata without discovering Task states" do
    project = project_fixture(%{})
    parent = list_fixture(project)

    socket =
      Workspace.assign_location(navigation_socket(), project, parent, false, false, false, [])

    child = list_fixture(project, parent)
    watch_location_queries()

    {:ok, ^project, ^child} =
      Workspace.resolve_location(%{"project_id" => project.id, "list_id" => child.id})

    socket = Workspace.assign_location(socket, project, child, false, false, false, [])
    assert Workspace.lists(socket) == [parent, child]
    assert socket.assigns.workspace.location_path == [parent, child]
    assert_receive :location_list_query
    refute_receive :location_list_query, 0
    refute_receive :availability_query, 0

    {:ok, renamed} = Taskman.Lists.rename_list(project, child, %{name: "Updated"})

    {:ok, ^project, ^renamed} =
      Workspace.resolve_location(%{"project_id" => project.id, "list_id" => child.id})

    socket = Workspace.assign_location(socket, project, renamed, false, false, false, [])
    assert socket.assigns.workspace.location_path == [parent, renamed]
    assert_receive :location_list_query
    refute_receive :location_list_query, 0
    refute_receive :availability_query, 0
  end

  defp navigation_socket do
    reconciliation_socket(State.new())
    |> Phoenix.Component.assign(
      :listing,
      TaskmanWeb.ProjectLive.Tasks.Listing.State.new([:pending])
    )
  end

  defp watch_location_queries do
    owner = self()
    handler = {__MODULE__, make_ref()}

    :ok =
      :telemetry.attach(
        handler,
        [:taskman, :repo, :query],
        fn _, _, metadata, owner ->
          if self() == owner do
            cond do
              String.starts_with?(metadata.query, "SELECT DISTINCT t0.\"list_id\", t0.\"status\"") ->
                send(owner, :availability_query)

              String.contains?(metadata.query, "FROM \"lists\" AS l0") and
                  String.contains?(metadata.query, "ORDER BY l0.\"inserted_at\"") ->
                send(owner, :location_list_query)

              true ->
                :ok
            end
          end
        end,
        owner
      )

    on_exit(fn -> :telemetry.detach(handler) end)
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
