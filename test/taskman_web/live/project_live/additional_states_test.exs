defmodule TaskmanWeb.ProjectLive.AdditionalStatesTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.ChangeNotifications.Event
  alias Taskman.Tasks
  alias Taskman.Tasks.Task
  alias TaskmanWeb.ProjectLive.{Locations, Workspace}

  setup %{conn: conn} do
    %{conn: log_in_user(conn, user_fixture())}
  end

  test "fresh listing observes analysis as selected while an explicit old filter excludes it", %{
    conn: conn
  } do
    project = project_fixture(%{})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}")
    open_filter(view)
    refute has_element?(view, "#task-status-filter-option-in_analysis")
    task = task_fixture(project, %{status: :in_analysis})
    assert has_element?(view, "#open-task-#{task.id}")
    assert has_element?(view, "#task-status-filter-option-in_analysis[checked]")

    {:ok, explicit, _} =
      live(conn, ~p"/projects/#{project.id}?statuses=icebox,pending,in_progress,in_review")

    open_filter(explicit)
    assert has_element?(explicit, "#task-status-filter-option-in_analysis")
    refute has_element?(explicit, "#task-status-filter-option-in_analysis[checked]")
    refute has_element?(explicit, "#open-task-#{task.id}")
  end

  test "creation reveal offers both additional states and persists analysis", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/new")
    assert has_element?(view, "#task-status-picker[data-action*='reveal_task_creation_states']")
    render_click(view, "reveal_task_creation_states", %{})

    assert has_element?(
             view,
             "#task-status-picker[data-visible-values*='in_analysis'][data-visible-values*='not_planned'][data-action='null']"
           )

    assert has_element?(view, "#task-status option[selected][value='pending']")

    view
    |> form("#task-form", task: %{title: "Investigate", status: "in_analysis"})
    |> render_submit()

    assert [%{status: :in_analysis}] = Tasks.list_tasks_for_project(project)
  end

  test "direct Project versus whole Project and List direct versus subtree", %{conn: conn} do
    project = project_fixture(%{})
    parent = list_fixture(project)
    child = list_fixture(project, parent)
    task_fixture(project, child, %{status: :not_planned})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}")
    open_filter(view)
    refute offered?(view)
    render_patch(view, ~p"/projects/#{project.id}?include_children=true")
    open_filter(view)
    assert offered?(view)
    render_patch(view, ~p"/projects/#{project.id}/lists/#{parent.id}?include_children=false")
    open_filter(view)
    refute offered?(view)
    render_patch(view, ~p"/projects/#{project.id}/lists/#{parent.id}?include_children=true")
    open_filter(view)
    assert offered?(view)
  end

  test "filtered-empty listing offers additional state unchecked and retains last use until remount",
       %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{status: :not_planned})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}")
    open_filter(view)
    assert offered?(view)
    refute has_element?(view, "#task-status-filter-option-not_planned[checked]")
    refute has_element?(view, "#open-task-#{task.id}")
    {:ok, _} = Tasks.update_task(project, task, %{status: :pending})
    assert offered?(view)
    {:ok, reloaded, _} = live(conn, ~p"/projects/#{project.id}")
    open_filter(reloaded)
    refute offered?(reloaded)
  end

  test "remembered absent selection survives checkbox edits and Project return", %{conn: conn} do
    project = project_fixture(%{})
    other = project_fixture(%{})
    task_fixture(project, %{status: :not_planned})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}?statuses=not_planned")
    render_patch(view, ~p"/projects/#{other.id}")
    open_filter(view)
    refute offered?(view)

    view
    |> form("#task-status-filter-form", status_filter: %{statuses: ["pending"]})
    |> render_change()

    render_patch(view, ~p"/projects/#{project.id}")
    open_filter(view)
    assert has_element?(view, "#task-status-filter-option-not_planned[checked]")
  end

  test "local persisted observation and external snapshots merge without reads or version ordering" do
    project = project_fixture(%{})
    socket = socket(project)

    event = %Event{
      entity: :task,
      operation: :created,
      project_id: project.id,
      entity_id: 123,
      fields: [],
      status: :not_planned,
      list_id: nil,
      lock_version: 2
    }

    assert Workspace.observe_task(socket, %Task{project_id: project.id, status: :not_planned}) ==
             socket

    assert Workspace.observe_task(socket, %Task{
             id: 123,
             project_id: project.id + 1,
             status: :not_planned
           }) == socket

    observed = Workspace.observe_task_event(socket, event)
    assert :not_planned in available(observed, project.id)
    assert Workspace.observe_task_event(observed, %{event | lock_version: 1}) == observed
    assert Workspace.observe_task_event(observed, event) == observed

    assert :not_planned in available(
             Workspace.observe_task(socket, %Task{
               id: 123,
               project_id: project.id,
               status: :not_planned
             }),
             project.id
           )

    for invalid <- [
          %{event | project_id: project.id + 1},
          %{event | status: :bogus},
          %{event | list_id: -1},
          %{event | fields: [:title], operation: :updated},
          %{event | fields: ["status"], operation: :updated},
          %{event | entity_id: nil}
        ] do
      assert Workspace.observe_task_event(socket, invalid) == socket
    end

    handler = "observations-#{System.unique_integer([:positive])}"
    parent = self()

    :ok =
      :telemetry.attach(
        handler,
        [:taskman, :repo, :query],
        fn _, _, metadata, _ ->
          if self() == parent, do: send(parent, {:query, metadata.query})
        end,
        nil
      )

    on_exit(fn -> :telemetry.detach(handler) end)
    Workspace.observe_task_event(socket, event)
    Workspace.observe_task(socket, %Task{id: 123, project_id: project.id, status: :not_planned})
    refute_received {:query, _}
  end

  test "local create, status save and move observe returned Tasks", %{conn: conn} do
    project = project_fixture(%{})
    destination = list_fixture(project)
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    view
    |> form("#task-form", task: %{title: "Observed create", status: "not_planned"})
    |> render_submit()

    assert_patch(view, ~p"/projects/#{project.id}")
    open_filter(view)
    assert offered?(view)
    task = task_fixture(project, destination, %{status: :pending})
    render_patch(view, ~p"/projects/#{project.id}/lists/#{destination.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{status: "not_planned"})
    |> render_change(%{"_target" => ["task", "status"]})

    open_filter(view)
    assert offered?(view)
    assert Tasks.get_task_for_project(project, task.id).status == :not_planned

    # A local move is self-excluded from notifications, so its destination must be observed directly.
    second = list_fixture(project)
    view |> element("#task-modal-close") |> render_click()
    render_patch(view, ~p"/projects/#{project.id}/lists/#{destination.id}?statuses=not_planned")
    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{second.id}") |> render_click()
    view |> element("#move-task-submit-#{task.id}") |> render_click()
    render_patch(view, ~p"/projects/#{project.id}/lists/#{second.id}")
    open_filter(view)
    assert offered?(view)
  end

  test "Project return resnapshots current use while preserving earlier observations", %{
    conn: conn
  } do
    project = project_fixture(%{})
    other = project_fixture(%{})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}")
    render_patch(view, ~p"/projects/#{other.id}")
    task = task_fixture(project, %{status: :not_planned})
    render_patch(view, ~p"/projects/#{project.id}")
    open_filter(view)
    assert offered?(view)
    {:ok, _} = Tasks.update_task(project, task, %{status: :pending})
    render_patch(view, ~p"/projects/#{other.id}")
    render_patch(view, ~p"/projects/#{project.id}")
    open_filter(view)
    assert offered?(view)
  end

  test "rendered hierarchy filter opening uses loaded hierarchy and selected List path", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task_list = list_fixture(project)
    root = task_fixture(project)
    selected = task_fixture(project, task_list, %{status: :pending}, parent: root)
    branch = task_fixture(project, %{}, parent: root)
    descendant = task_fixture(project, %{}, parent: branch)
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{selected.id}")

    assert has_element?(
             view,
             "#task-hierarchy-node-#{root.id} #task-hierarchy-node-#{selected.id}"
           )

    assert has_element?(view, "#task-location-list-#{task_list.id}")

    handler = "rendered-filter-#{System.unique_integer([:positive])}"
    parent = self()
    view_pid = view.pid

    :ok =
      :telemetry.attach(
        handler,
        [:taskman, :repo, :query],
        fn _, _, metadata, _ ->
          if self() == view_pid, do: send(parent, {:filter_query, metadata.query})
        end,
        nil
      )

    on_exit(fn -> :telemetry.detach(handler) end)

    view |> element("#task-hierarchy-status-filter-button") |> render_click()
    assert has_element?(view, "#task-hierarchy-status-filter-form")

    assert has_element?(
             view,
             "#task-hierarchy-node-#{root.id} #task-hierarchy-node-#{selected.id}"
           )

    assert has_element?(view, "#task-location-list-#{task_list.id}")
    render_click(view, "close_hierarchy_status_filter")
    refute has_element?(view, "#task-hierarchy-status-filter-form")
    assert has_element?(view, "#task-hierarchy-node-#{selected.id}")
    view |> element("#task-hierarchy-disclosure-#{branch.id}") |> render_click()
    assert has_element?(view, "#task-hierarchy-node-#{descendant.id}")
    refute_received {:filter_query, _}

    view |> element("#task-hierarchy-link-#{branch.id}") |> render_click()
    assert has_element?(view, "#task-hierarchy-link-#{branch.id}[aria-current='true']")
    refute has_element?(view, "#task-location-list-#{task_list.id}")
  end

  test "filter handlers and option calculation perform no persistence reads" do
    alias TaskmanWeb.ProjectLive.Tasks.{Editing, Hierarchy, Listing}
    project = project_fixture(%{})
    handler = "filters-#{System.unique_integer([:positive])}"
    parent = self()

    :ok =
      :telemetry.attach(
        handler,
        [:taskman, :repo, :query],
        fn _, _, metadata, _ ->
          if self() == parent, do: send(parent, {:query, metadata.query})
        end,
        nil
      )

    on_exit(fn -> :telemetry.detach(handler) end)
    hierarchy = Hierarchy.empty()
    socket = socket(project)
    socket = Phoenix.Component.assign(socket, :listing, Listing.State.new([:pending]))

    socket =
      Phoenix.Component.assign(socket, :editing, %{Editing.State.empty() | hierarchy: hierarchy})

    {:noreply, opened} = Editing.handle_event("toggle_hierarchy_status_filter", %{}, socket)
    assert opened.assigns.editing.hierarchy.filter_open?

    assert Hierarchy.filter_statuses(hierarchy, []).visible_statuses == [
             :in_analysis,
             :not_planned
           ]

    {:noreply, opened} = Listing.handle_event("toggle_task_status_filter", %{}, opened)
    assert opened.assigns.listing.filter_open?

    TaskmanWeb.ProjectLive.Tasks.StateOptions.for_form(
      socket.assigns.workspace.locations,
      project.id,
      nil,
      :pending,
      false
    )

    Workspace.available_statuses(socket.assigns.workspace)
    refute_received {:query, _}
  end

  test "reconnect validates server Project identity and merges current state conservatively", %{
    conn: conn
  } do
    project = project_fixture(%{})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}?statuses=pending")
    assert has_element?(view, "#workspace-content[data-project-id='#{project.id}']")
    # Exclude publication to model missed delivery; a reconnect must still discover current state.
    Taskman.Repo.insert!(
      Ecto.Changeset.change(%Task{project_id: project.id}, title: "Missed", status: :not_planned)
    )

    render_hook(view, "resnapshot_task_locations", %{"project_id" => "invalid"})

    render_hook(view, "resnapshot_task_locations", %{
      "project_id" => Integer.to_string(project.id + 1)
    })

    render_hook(view, "resnapshot_task_locations", %{})
    open_filter(view)
    refute offered?(view)

    render_hook(view, "resnapshot_task_locations", %{
      "project_id" => Integer.to_string(project.id)
    })

    assert offered?(view)
  end

  test "reconnect ignores absent or stale subscription context and preserves creation drafts", %{
    conn: conn
  } do
    project = project_fixture(%{})
    socket = socket(project)

    socket =
      Phoenix.Component.assign(socket, :workspace, %{
        socket.assigns.workspace
        | subscribed_project_id: nil
      })

    assert {:noreply, ^socket} =
             Workspace.handle_event(
               "resnapshot_task_locations",
               %{"project_id" => project.id},
               socket
             )

    socket =
      Phoenix.Component.assign(socket, :workspace, %{
        socket.assigns.workspace
        | selected_project: nil
      })

    assert {:noreply, ^socket} =
             Workspace.handle_event(
               "resnapshot_task_locations",
               %{"project_id" => project.id},
               socket
             )

    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/new?statuses=pending")

    view
    |> form("#task-form", task: %{title: "Keep this draft", status: "not_planned"})
    |> render_change()

    # Preference changes clean the browser query; reconnection identity remains server-owned.
    open_filter(view)

    view
    |> form("#task-status-filter-form", status_filter: %{statuses: ["pending"]})
    |> render_change()

    render_hook(view, "resnapshot_task_locations", %{
      "project_id" => Integer.to_string(project.id)
    })

    assert has_element?(view, "#task-title[value='Keep this draft']")
    assert has_element?(view, "#task-status option[selected][value='not_planned']")
    refute offered?(view)
  end

  test "edit reveal changes only dialog presentation and survives autosave and Task navigation",
       %{conn: conn} do
    project = project_fixture(%{})
    root = task_fixture(project)
    child = task_fixture(project, %{}, parent: root)
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/tasks/#{root.id}")
    assert has_element?(view, "#task-status-picker[data-action*='reveal_task_edit_states']")
    assert has_element?(view, "#task-status option[value='not_planned']")
    before = :sys.get_state(view.pid).socket.assigns.editing.autosave
    assert_no_queries(view, fn -> render_click(view, "reveal_task_edit_states", %{}) end)
    assert :sys.get_state(view.pid).socket.assigns.editing.autosave == before
    assert Tasks.get_task_for_project(project, root.id).lock_version == root.lock_version

    assert has_element?(
             view,
             "#task-status-picker[data-visible-values*='not_planned'][data-action='null']"
           )

    view
    |> form("#task-form", task: %{priority: "high"})
    |> render_change(%{"_target" => ["task", "priority"]})

    assert has_element?(
             view,
             "#task-status-picker[data-visible-values*='not_planned'][data-action='null']"
           )

    view |> element("#task-hierarchy-link-#{child.id}") |> render_click()

    assert has_element?(
             view,
             "#task-status-picker[data-visible-values*='not_planned'][data-action='null']"
           )

    view |> element("#task-modal-close") |> render_click()
    render_patch(view, ~p"/projects/#{project.id}/tasks/#{root.id}")
    assert has_element?(view, "#task-status-picker[data-action*='reveal_task_edit_states']")
  end

  test "creation scope uses owning List and retains drafts and reveal through validation", %{
    conn: conn
  } do
    project = project_fixture(%{})
    observed = list_fixture(project)
    empty = list_fixture(project)
    task_fixture(project, observed, %{status: :not_planned})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{empty.id}/tasks/new")
    assert has_element?(view, "#task-status-picker[data-action*='reveal_task_creation_states']")

    view
    |> form("#task-form", task: %{title: "Draft"}, location: "list:#{observed.id}")
    |> render_change()

    assert has_element?(
             view,
             "#task-status-picker[data-visible-values*='not_planned'][data-action*='reveal_task_creation_states']"
           )

    view
    |> form("#task-form",
      task: %{title: "Draft", status: "not_planned"},
      location: "list:#{empty.id}"
    )
    |> render_change()

    assert has_element?(
             view,
             "#task-status-picker[data-visible-values*='not_planned'][data-action*='reveal_task_creation_states']"
           )

    view |> form("#task-form", task: %{title: "Draft", status: "pending"}) |> render_change()
    assert has_element?(view, "#task-status-picker[data-action*='reveal_task_creation_states']")
    before = :sys.get_state(view.pid).socket.assigns.creation.form
    assert_no_queries(view, fn -> render_click(view, "reveal_task_creation_states", %{}) end)
    assert :sys.get_state(view.pid).socket.assigns.creation.form == before
    view |> form("#task-form", task: %{title: "", status: "pending"}) |> render_change()

    assert has_element?(
             view,
             "#task-status-picker[data-visible-values*='not_planned'][data-action='null']"
           )

    view
    |> form("#task-form", task: %{title: "Draft"}, location: "list:#{observed.id}")
    |> render_change()

    view
    |> form("#task-form", task: %{title: "Draft"}, location: "list:#{empty.id}")
    |> render_change()

    assert has_element?(
             view,
             "#task-status-picker[data-visible-values*='not_planned'][data-action='null']"
           )

    render_patch(view, ~p"/projects/#{project.id}/lists/#{empty.id}")
    render_patch(view, ~p"/projects/#{project.id}/lists/#{empty.id}/tasks/new")
    assert has_element?(view, "#task-status-picker[data-action*='reveal_task_creation_states']")
    render_patch(view, ~p"/projects/#{project.id}/tasks/new")

    assert has_element?(
             view,
             "#task-status-picker[data-visible-values*='not_planned'][data-action*='reveal_task_creation_states']"
           )
  end

  test "recovery disables the state control and ignores forged reveal events", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project)
    task = task_fixture(project, lost, %{})
    {:ok, view, _} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: ""})
    |> render_change(%{"_target" => ["task", "title"]})

    Taskman.Repo.delete!(task)
    Taskman.Repo.delete!(lost)

    send(view.pid, %Event{
      entity: :list,
      operation: :updated,
      project_id: project.id,
      entity_id: lost.id,
      fields: [:name]
    })

    assert has_element?(
             view,
             "#task-recovery #task-status[disabled] option[selected][value='pending']"
           )

    before = :sys.get_state(view.pid).socket.assigns

    assert_no_queries(view, fn ->
      render_click(view, "reveal_task_edit_states", %{})
      render_click(view, "reveal_task_creation_states", %{})
    end)

    after_reveal = :sys.get_state(view.pid).socket.assigns
    assert after_reveal.editing == before.editing
    assert after_reveal.creation == before.creation
    assert after_reveal.recovery == before.recovery

    assert has_element?(
             view,
             "#task-recovery #task-status[disabled] option[selected][value='pending']"
           )
  end

  defp assert_no_queries(view, action) do
    handler = "reveal-#{System.unique_integer([:positive])}"
    parent = self()
    view_pid = view.pid

    :ok =
      :telemetry.attach(
        handler,
        [:taskman, :repo, :query],
        fn _, _, metadata, _ ->
          if self() == view_pid, do: send(parent, {:reveal_query, metadata.query})
        end,
        nil
      )

    try do
      action.()
      refute_received {:reveal_query, _}
    after
      :telemetry.detach(handler)
    end
  end

  defp socket(project) do
    %Phoenix.LiveView.Socket{
      assigns: %{
        __changed__: %{},
        workspace: %Workspace.State{selected_project: project, subscribed_project_id: project.id}
      }
    }
  end

  defp available(socket, id),
    do: Locations.available(socket.assigns.workspace.locations, id, nil, :direct)

  defp open_filter(view), do: view |> element("#task-status-filter-button") |> render_click()
  defp offered?(view), do: has_element?(view, "#task-status-filter-option-not_planned")
end
