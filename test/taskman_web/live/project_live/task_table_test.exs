defmodule TaskmanWeb.ProjectLive.TaskTableTest do
  use TaskmanWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  setup %{conn: conn} do
    {:ok, conn: log_in_user(conn, user_fixture())}
  end

  test "Share is available only for a hydrated valid Project Task view", %{conn: conn} do
    project = project_fixture(%{})
    task_list = list_fixture(project)
    task = task_fixture(project, task_list, %{})

    {:ok, root, _html} = live(conn, ~p"/")
    refute has_element?(root, "#share-task-view")

    {:ok, missing_project, _html} = live(conn, "/projects/999999999")
    refute has_element?(missing_project, "#share-task-view")

    {:ok, missing_list, _html} = live(conn, "/projects/#{project.id}/lists/999999999")
    refute has_element?(missing_list, "#share-task-view")

    for path <- [
          ~p"/projects/#{project.id}",
          ~p"/projects/#{project.id}/lists/#{task_list.id}",
          ~p"/projects/#{project.id}/lists/#{task_list.id}/tasks/#{task.id}"
        ] do
      {:ok, view, _html} = live(conn, path)
      assert has_element?(view, "#share-task-view[disabled][aria-label='Share current view']")

      if String.contains?(path, "/tasks/") do
        assert has_element?(
                 view,
                 "#task-modal-content #task-modal-share-task-view[disabled][aria-label='Share Task']"
               )
      else
        refute has_element?(view, "#task-modal-share-task-view")
      end

      render_hook(view, "hydrate_task_table_preferences", %{
        "include_children" => true,
        "statuses" => ["done", "pending", "done"]
      })

      assert has_element?(
               view,
               "#share-task-view:not([disabled])[data-include-children='true'][data-statuses='pending,done']"
             )

      if String.contains?(path, "/tasks/") do
        assert has_element?(
                 view,
                 "#task-modal-share-task-view:not([disabled])[data-include-children='true'][data-statuses='pending,done']"
               )

        assert has_element?(
                 view,
                 "#task-detail-actions #task-modal-share-task-view[data-share-path='#{path}']"
               )

        assert has_element?(
                 view,
                 "#task-detail-actions > div:first-child #task-modal-share-task-view"
               )

        assert has_element?(
                 view,
                 "#task-detail-actions > button:last-child#move-task-detail-button-#{task.id}"
               )
      end

      assert has_element?(
               view,
               "#share-task-view[phx-hook='TaskmanWeb.ProjectLive.ShareTaskView']"
             )

      assert has_element?(view, "#share-task-view-status[role='status'][aria-live='polite']")
      refute has_element?(view, "#share-task-view-feedback[hidden] #share-task-view-status")
      assert has_element?(view, "#share-task-view-url[readonly][hidden]")
    end
  end

  test "Share reflects an empty status selection and filter changes", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}?statuses=done")

    render_hook(view, "hydrate_task_table_preferences", %{
      "include_children" => false,
      "statuses" => []
    })

    assert has_element?(
             view,
             "#share-task-view[data-include-children='false'][data-statuses='done']"
           )

    view |> element("#include-child-lists") |> render_click()

    assert has_element?(
             view,
             "#share-task-view[data-include-children='true'][data-statuses='done']"
           )

    render_patch(view, "/projects/#{project.id}?statuses=")
    assert has_element?(view, "#share-task-view[data-include-children='true'][data-statuses='']")
  end

  test "hydration applies both preferences and ignores a duplicate hydration event", %{conn: conn} do
    project = project_fixture(%{})
    child = list_fixture(project)
    task = task_fixture(project, child, %{status: :done})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    refute view_assigns(view).preferences_hydrated?
    refute has_element?(view, "#task-#{task.id}")

    render_hook(view, "hydrate_task_table_preferences", %{
      "include_children" => true,
      "statuses" => ["done", "done", "bogus"]
    })

    assert view_assigns(view).preferences_hydrated?
    assert has_element?(view, "#include-child-lists[aria-pressed='true']")
    assert has_element?(view, "#task-#{task.id}")
    assert view_assigns(view).listing.visible_statuses == [:done]

    render_hook(view, "hydrate_task_table_preferences", %{
      "include_children" => false,
      "statuses" => []
    })

    assert has_element?(view, "#task-#{task.id}")
  end

  test "a route snapshot overrides one mounted filter and a clean route keeps both", %{conn: conn} do
    project = project_fixture(%{})
    task_list = list_fixture(project)
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    render_hook(view, "hydrate_task_table_preferences", %{
      "include_children" => true,
      "statuses" => ["done"]
    })

    render_patch(view, ~p"/projects/#{project.id}/lists/#{task_list.id}?statuses=pending")
    assert has_element?(view, "#include-child-lists[aria-pressed='true']")
    assert view_assigns(view).listing.visible_statuses == [:pending]

    view |> element("#project-tasks-link") |> render_click()
    assert_patch(view, ~p"/projects/#{project.id}")
    assert has_element?(view, "#include-child-lists[aria-pressed='true']")
    assert view_assigns(view).listing.visible_statuses == [:pending]
  end

  test "duplicate filter keys cannot overwrite hydrated or mounted preferences", %{conn: conn} do
    project = project_fixture(%{})
    task_list = list_fixture(project)
    task = task_fixture(project, task_list, %{status: :done})

    path =
      "/projects/#{project.id}?include_children=false&include_children=false&statuses=pending&statuses=bogus"

    {:ok, view, _html} = live(conn, path)

    render_hook(view, "hydrate_task_table_preferences", %{
      "include_children" => true,
      "statuses" => ["done"]
    })

    assert has_element?(view, "#include-child-lists[aria-pressed='true']")
    assert has_element?(view, "#task-#{task.id}")
    assert has_element?(view, "#share-task-view[data-statuses='done']")

    render_patch(view, "/projects/#{project.id}")
    render_patch(view, path)
    assert has_element?(view, "#include-child-lists[aria-pressed='true']")
    assert has_element?(view, "#task-#{task.id}")
    assert has_element?(view, "#share-task-view[data-statuses='done']")

    render_patch(
      view,
      "/projects/#{project.id}?statuses=bogus&statuses=pending&include_children=false"
    )

    assert has_element?(view, "#include-child-lists[aria-pressed='false']")
    assert has_element?(view, "#share-task-view[data-statuses='done']")
  end

  test "the include switch changes the Task stream without patch navigation", %{conn: conn} do
    project = project_fixture(%{})
    child = list_fixture(project)
    task = task_fixture(project, child, %{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}?statuses=pending")

    refute has_element?(view, "#task-#{task.id}")
    view |> element("#include-child-lists") |> render_click()
    assert has_element?(view, "#task-#{task.id}")
    refute_patched(view, ~p"/projects/#{project.id}?include_children=true")
  end

  test "explicit root parameters each override a conflicting browser hydration value", %{
    conn: conn
  } do
    {:ok, include_view, _html} = live(conn, "/?include_children=false")

    render_hook(include_view, "hydrate_task_table_preferences", %{
      "include_children" => true,
      "statuses" => ["done"]
    })

    refute view_assigns(include_view).workspace.include_children?
    assert view_assigns(include_view).listing.visible_statuses == [:done]
    assert view_assigns(include_view).preferences_hydrated?

    {:ok, status_view, _html} = live(conn, "/?statuses=")

    render_hook(status_view, "hydrate_task_table_preferences", %{
      "include_children" => true,
      "statuses" => ["done"]
    })

    assert view_assigns(status_view).workspace.include_children?
    assert view_assigns(status_view).listing.visible_statuses == []
  end

  test "missing List recovery retains filters through a clean Project route", %{conn: conn} do
    project = project_fixture(%{})

    {:ok, view, _html} =
      live(conn, "/projects/#{project.id}/lists/999999999?include_children=true&statuses=done")

    assert has_element?(view, "#location-not-found")

    render_hook(view, "hydrate_task_table_preferences", %{
      "include_children" => true,
      "statuses" => ["done"]
    })

    render_patch(view, ~p"/projects/#{project.id}")
    assert has_element?(view, "#include-child-lists[aria-pressed='true']")
    assert view_assigns(view).listing.visible_statuses == [:done]
  end

  test "stale hydration and route snapshot events cannot replace the active route", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    render_hook(view, "hydrate_task_table_preferences", %{
      "route_key" => "/previous-route?statuses=done",
      "include_children" => true,
      "statuses" => ["done"]
    })

    assert view_assigns(view).preferences_hydrated?
    refute view_assigns(view).workspace.include_children?
    assert :pending in view_assigns(view).listing.visible_statuses

    render_hook(view, "apply_task_table_route_snapshot", %{
      "route_key" => "/previous-route?statuses=done",
      "include_children" => true,
      "statuses" => ["done"]
    })

    refute view_assigns(view).workspace.include_children?
    assert :pending in view_assigns(view).listing.visible_statuses
  end

  test "hides Will Not Do by default and restores a browser status selection", %{conn: conn} do
    project = project_fixture(%{})
    pending = task_fixture(project, %{title: "Pending", status: :pending})
    rejected = task_fixture(project, %{title: "Rejected", status: :will_not_do})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    assert has_element?(view, "#task-#{pending.id}")
    refute has_element?(view, "#task-#{rejected.id}")

    render_hook(view, "hydrate_task_table_preferences", %{
      "include_children" => false,
      "statuses" => ["will_not_do"]
    })

    refute has_element?(view, "#task-#{pending.id}")
    assert has_element?(view, "#task-#{rejected.id}")
  end

  test "applies checkbox status changes immediately and renders a filtered empty state", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Pending", status: :pending})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#task-status-filter-button") |> render_click()

    view
    |> form("#task-status-filter-form", status_filter: %{statuses: ["done"]})
    |> render_change()

    refute has_element?(view, "#task-#{task.id}")
    assert has_element?(view, "#tasks-empty", "No tasks match the selected statuses")
  end

  test "keeps location-specific empty copy when the location genuinely has no Tasks", %{
    conn: conn
  } do
    project = project_fixture(%{})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    assert has_element?(view, "#tasks-empty", "No direct tasks yet")
  end

  test "opens and dismisses the status dropdown without changing its selection", %{conn: conn} do
    project = project_fixture(%{})
    _task = task_fixture(project, %{})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    assert has_element?(
             view,
             "#workspace-content[phx-hook='TaskmanWeb.ProjectLive.TaskTablePreferences']"
           )

    assert has_element?(
             view,
             "#workspace-content[data-task-statuses='icebox,pending,in_progress,in_review,done,will_not_do']"
           )

    assert has_element?(view, "#task-status-filter-button[aria-expanded='false']")
    refute has_element?(view, "#task-status-filter-menu")

    view |> element("#task-status-filter-button") |> render_click()

    assert has_element?(view, "#task-status-filter-button[aria-expanded='true']")

    assert has_element?(
             view,
             "#task-status-filter[phx-click-away='close_task_status_filter'][phx-window-keydown='close_task_status_filter'][phx-key='escape']"
           )

    assert has_element?(view, "#task-status-filter-menu")
    assert has_element?(view, "#task-status-filter-option-done[checked]")
    refute has_element?(view, "#task-status-filter-option-will_not_do[checked]")

    view |> element("#task-status-filter-button") |> render_click()
    refute has_element?(view, "#task-status-filter-menu")

    view |> element("#task-status-filter-button") |> render_click()
    view |> element("#task-status-filter") |> render_keydown(%{"key" => "Escape"})
    refute has_element?(view, "#task-status-filter-menu")
  end

  test "sorts titles ascending on first click and descending on the second", %{conn: conn} do
    project = project_fixture(%{})
    beta = task_fixture(project, %{title: "Beta"})
    alpha = task_fixture(project, %{title: "alpha"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#sort-task-title") |> render_click()

    assert has_element?(view, "#task-title-header[aria-sort='ascending']")
    assert has_element?(view, "#tasks > #tasks-#{alpha.id} + #tasks-#{beta.id}")

    view |> element("#sort-task-title") |> render_click()

    assert has_element?(view, "#task-title-header[aria-sort='descending']")
    assert has_element?(view, "#tasks > #tasks-#{beta.id} + #tasks-#{alpha.id}")
  end

  test "uses descending initial sorts for status and priority", %{conn: conn} do
    project = project_fixture(%{})

    pending_urgent =
      task_fixture(project, %{title: "Pending urgent", status: :pending, priority: :urgent})

    done_low =
      task_fixture(project, %{title: "Done low", status: :done, priority: :low})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#sort-task-status") |> render_click()

    assert has_element?(view, "#task-status-header[aria-sort='descending']")
    assert has_element?(view, "#tasks > #tasks-#{done_low.id} + #tasks-#{pending_urgent.id}")

    view |> element("#sort-task-priority") |> render_click()

    assert has_element?(view, "#task-priority-header[aria-sort='descending']")
    assert has_element?(view, "#tasks > #tasks-#{pending_urgent.id} + #tasks-#{done_low.id}")
  end

  test "sorts IDs ascending initially and exposes every non-action sort control", %{conn: conn} do
    project = project_fixture(%{})
    first = task_fixture(project, %{title: "First"})
    second = task_fixture(project, %{title: "Second"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    assert has_element?(view, "#sort-task-id")
    assert has_element?(view, "#sort-task-title")
    assert has_element?(view, "#sort-task-status")
    assert has_element?(view, "#sort-task-priority")
    refute has_element?(view, "#sort-task-actions")

    view |> element("#sort-task-id") |> render_click()

    assert has_element?(view, "#task-number-header[aria-sort='ascending']")
    assert has_element?(view, "#tasks > #tasks-#{first.id} + #tasks-#{second.id}")
  end

  test "sorts descendant locations ascending on first click", %{conn: conn} do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    delivery = list_fixture(project, nil, %{name: "Delivery"})
    planning_task = task_fixture(project, planning, %{title: "Planning"})
    delivery_task = task_fixture(project, delivery, %{title: "Delivery"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}?include_children=true")

    assert has_element?(view, "#sort-task-location")
    view |> element("#sort-task-location") |> render_click()

    assert has_element?(view, "#task-location-header[aria-sort='ascending']")

    assert has_element?(
             view,
             "#tasks > #tasks-#{delivery_task.id} + #tasks-#{planning_task.id}"
           )
  end

  test "clears the Location sort when child Lists are excluded", %{conn: conn} do
    project = project_fixture(%{})
    task_list = list_fixture(project, nil, %{name: "Planning"})
    first_direct = task_fixture(project, %{title: "First direct"})
    second_direct = task_fixture(project, %{title: "Second direct"})
    _list_task = task_fixture(project, task_list, %{title: "List task"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}?include_children=true")

    view |> element("#sort-task-location") |> render_click()
    view |> element("#sort-task-location") |> render_click()

    assert has_element?(view, "#task-location-header[aria-sort='descending']")

    assert has_element?(
             view,
             "#tasks > #tasks-#{second_direct.id} + #tasks-#{first_direct.id}"
           )

    view |> element("#include-child-lists") |> render_click()

    refute_patched(view, ~p"/projects/#{project.id}")
    refute has_element?(view, "#task-location-header")

    assert has_element?(
             view,
             "#tasks > #tasks-#{first_direct.id} + #tasks-#{second_direct.id}"
           )
  end

  defp view_assigns(view) do
    %{socket: socket} = :sys.get_state(view.pid)
    socket.assigns
  end
end
