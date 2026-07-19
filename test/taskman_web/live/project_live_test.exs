defmodule TaskmanWeb.ProjectLiveTest do
  use TaskmanWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  test "root renders the list-first empty state", %{conn: conn} do
    {:ok, view, _html} = live(conn, ~p"/")

    assert has_element?(view, "#project-sidebar")
    assert has_element?(view, "#project-form")
    assert has_element?(view, "#main-panel[data-state='no-selection']")
  end

  test "selected workspace and Task modal use dark surfaces", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    assert has_element?(view, "#main-panel.bg-slate-900")
    assert has_element?(view, "#tasks.bg-slate-900")
    assert has_element?(view, "#task-modal-content.bg-slate-900")
  end

  test "creates a Project and selects it in the URL", %{conn: conn} do
    {:ok, view, _html} = live(conn, ~p"/")

    view
    |> form("#project-form", project: %{name: "Taskman", primary_directory: File.cwd!()})
    |> render_submit()

    assert [project] = Taskman.Projects.list_projects()
    assert_patch(view, ~p"/projects/#{project.id}")
    assert has_element?(view, "#project-#{project.id}[aria-current='page']")
    assert has_element?(view, "#tasks")
  end

  test "unparseable Project ID keeps the sidebar usable", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, "/projects/not-an-id")

    assert has_element?(view, "#project-sidebar")
    assert has_element?(view, "#project-not-found")
    refute has_element?(view, "#add-task")

    view |> element("#project-#{project.id}") |> render_click()
    assert_patch(view, ~p"/projects/#{project.id}")
    assert has_element?(view, "#tasks")
  end

  test "selected Project shows only its direct Tasks", %{conn: conn} do
    selected = project_fixture(%{})
    other = project_fixture(%{})
    selected_task = task_fixture(selected, %{title: "Visible"})
    other_task = task_fixture(other, %{title: "Hidden"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{selected.id}")

    assert has_element?(view, "#task-#{selected_task.id}")
    refute has_element?(view, "#task-#{other_task.id}")
  end

  test "populated Task list renders column headers with uniform cell padding", %{conn: conn} do
    project = project_fixture(%{})
    _task = task_fixture(project, %{})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    assert has_element?(view, "#task-table-header.px-3.py-3")
    assert has_element?(view, "#task-table-header + #tasks")
    assert has_element?(view, "#tasks article.px-3.py-3")
  end

  test "invalid Project input renders inline errors", %{conn: conn} do
    {:ok, view, _html} = live(conn, ~p"/")

    view
    |> form("#project-form", project: %{name: "Taskman", primary_directory: "/not/a/taskman/dir"})
    |> render_submit()

    assert has_element?(view, "#project-form [data-role='field-error']")
  end

  test "opens and cancels the new Task modal over the selected list", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Existing"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#add-task") |> render_click()
    assert_patch(view, ~p"/projects/#{project.id}/tasks/new")
    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-#{task.id}")

    view |> element("#cancel-task") |> render_click()
    assert_patch(view, ~p"/projects/#{project.id}")
    refute has_element?(view, "#task-modal")
  end

  test "successful Task hides the empty state after validation errors", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    view |> form("#task-form", task: %{title: ""}) |> render_submit()
    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-form [data-role='field-error']")

    view |> form("#task-form", task: %{title: "Ship first slice"}) |> render_submit()
    assert_patch(view, ~p"/projects/#{project.id}")
    refute has_element?(view, "#task-modal")

    [task] = Taskman.Tasks.list_tasks_for_project(project)
    assert task.status == :pending
    assert task.priority == :none
    assert has_element?(view, "#task-#{task.id}")
    assert has_element?(view, "#tasks-empty.hidden.only\\:block")
  end
end
