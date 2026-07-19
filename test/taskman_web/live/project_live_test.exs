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
end
