defmodule TaskmanWeb.ProjectLiveTaskTableTest do
  use TaskmanWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  setup %{conn: conn} do
    {:ok, conn: log_in_user(conn, user_fixture())}
  end

  test "hides Will Not Do by default and restores a browser status selection", %{conn: conn} do
    project = project_fixture(%{})
    pending = task_fixture(project, %{title: "Pending", status: :pending})
    rejected = task_fixture(project, %{title: "Rejected", status: :will_not_do})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    assert has_element?(view, "#task-#{pending.id}")
    refute has_element?(view, "#task-#{rejected.id}")

    render_hook(view, "restore_task_statuses", %{"statuses" => ["will_not_do"]})

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
             "#task-status-filter[phx-hook='TaskmanWeb.TaskComponents.TaskStatusFilterStorage']"
           )

    assert has_element?(
             view,
             "#task-status-filter[data-task-statuses='icebox,pending,in_progress,in_review,done,will_not_do']"
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

    assert_patch(view, ~p"/projects/#{project.id}")
    refute has_element?(view, "#task-location-header")

    assert has_element?(
             view,
             "#tasks > #tasks-#{first_direct.id} + #tasks-#{second_direct.id}"
           )
  end
end
