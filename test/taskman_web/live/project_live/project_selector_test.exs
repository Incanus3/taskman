defmodule TaskmanWeb.ProjectLive.ProjectSelectorTest do
  use TaskmanWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Projects

  setup %{conn: conn}, do: {:ok, conn: log_in_user(conn, user_fixture())}

  test "neutral selector opens an empty Project menu and new Project modal", %{conn: conn} do
    {:ok, view, _html} = live(conn, ~p"/")

    assert has_element?(view, "#project-selector-toggle[aria-expanded='false']")
    refute has_element?(view, "#project-edit-button")
    assert has_element?(view, "#new-project-button")

    view |> element("#project-selector-toggle") |> render_click()
    assert has_element?(view, "#project-selector-menu[aria-label='Projects']")
    assert has_element?(view, "#project-selector-empty")

    view |> element("#project-selector-menu") |> render_keydown(%{"key" => "Escape"})
    refute has_element?(view, "#project-selector-menu")

    view |> element("#new-project-button") |> render_click()
    assert has_element?(view, "#project-modal")
    assert has_element?(view, "#project-form")
    assert has_element?(view, "#project-color[value='6366F1']")
  end

  test "selected selector shows metadata and switches via a Project link", %{conn: conn} do
    first = project_fixture(%{name: "First", description: "A Project"})
    second = project_fixture(%{name: "Second"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{first.id}")

    assert has_element?(view, "#project-selector-name", "First")
    assert has_element?(view, "#project-selector-description", "A Project")
    assert has_element?(view, "#project-edit-button[aria-label='Edit Project']")
    assert has_element?(view, "#project-selector-identity[aria-label='Open Project choices']")
    assert has_element?(view, "#project-selector-toggle[aria-label='Choose Project']")

    view |> element("#project-selector-toggle") |> render_click()
    assert has_element?(view, "#project-selector-identity[aria-label='Close Project choices']")
    assert has_element?(view, "#select-project-#{first.id}[aria-current='page']")
    assert has_element?(view, "#select-project-#{second.id}")
    view |> element("#select-project-#{second.id}") |> render_click()
    assert_patch(view, ~p"/projects/#{second.id}")
    assert has_element?(view, "#project-selector-toggle[aria-expanded='false']")
  end

  test "outside click dismisses Project choices and the editor", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#project-selector-toggle") |> render_click()
    assert has_element?(view, "#project-selector[phx-click-away='close_project_selector']")
    render_click(view, "close_project_selector", %{})
    refute has_element?(view, "#project-selector-menu")

    view |> element("#project-edit-button") |> render_click()
    assert has_element?(view, "#project-modal-content[phx-click-away]")
    render_click(view, "cancel_project_edit", %{})
    refute has_element?(view, "#project-modal")
    assert has_element?(view, "#project-selector-name", project.name)
  end

  test "mobile Project navigation opens and dismisses", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    assert has_element?(view, "#project-sidebar-toggle[aria-expanded='false']")
    refute has_element?(view, "#project-sidebar-backdrop")

    view |> element("#project-sidebar-toggle") |> render_click()
    assert has_element?(view, "#project-sidebar-toggle[aria-expanded='true']")
    assert has_element?(view, "#project-sidebar-backdrop")

    view |> element("#project-sidebar-close") |> render_click()
    assert has_element?(view, "#project-sidebar-toggle[aria-expanded='false']")
    refute has_element?(view, "#project-sidebar-backdrop")

    view |> element("#project-sidebar-toggle") |> render_click()
    view |> element("#project-sidebar-backdrop") |> render_click()
    assert has_element?(view, "#project-sidebar-toggle[aria-expanded='false']")
  end

  test "selecting a Project or List closes mobile navigation", %{conn: conn} do
    first = project_fixture(%{name: "First"})
    second = project_fixture(%{name: "Second"})
    task_list = list_fixture(first)
    {:ok, view, _html} = live(conn, ~p"/projects/#{first.id}")

    view |> element("#project-sidebar-toggle") |> render_click()
    view |> element("#select-list-#{task_list.id}") |> render_click()
    assert_patch(view, ~p"/projects/#{first.id}/lists/#{task_list.id}")
    assert has_element?(view, "#project-sidebar-toggle[aria-expanded='false']")

    view |> element("#project-sidebar-toggle") |> render_click()
    view |> element("#project-selector-toggle") |> render_click()
    view |> element("#select-project-#{second.id}") |> render_click()
    assert_patch(view, ~p"/projects/#{second.id}")
    assert has_element?(view, "#project-sidebar-toggle[aria-expanded='false']")
  end

  test "create validates fields and selects the new Project", %{conn: conn} do
    {:ok, view, _html} = live(conn, ~p"/")
    view |> element("#new-project-button") |> render_click()

    view
    |> form("#project-form", project: %{name: "", color: "BAD", icon: "briefcase"})
    |> render_submit()

    assert has_element?(view, "#project-form [data-role='field-error']")
    assert has_element?(view, "#project-colors [data-role='field-error']")
    assert has_element?(view, "#project-color[value='BAD']")

    view
    |> form("#project-form",
      project: %{name: "Created", description: "Description", color: "12abef", icon: "beaker"}
    )
    |> render_submit()

    assert [project] = Projects.list_projects()
    assert project.color == "#12ABEF"
    assert project.icon == "beaker"
    assert_patch(view, ~p"/projects/#{project.id}")
    refute has_element?(view, "#project-modal")
  end

  test "edit preserves a List route and a non-preset color", %{conn: conn} do
    project = project_fixture(%{name: "Before", color: "#12ABEF"})
    task_list = list_fixture(project)
    path = ~p"/projects/#{project.id}/lists/#{task_list.id}"
    {:ok, view, _html} = live(conn, path)

    view |> element("#project-edit-button") |> render_click()
    assert has_element?(view, "#project-color[value='12ABEF']")
    assert has_element?(view, "#project-icon-beaker[type='radio']")

    view
    |> form("#project-form",
      project: %{name: "After", description: "Updated", icon: "briefcase", color: "12ABEF"}
    )
    |> render_submit()

    assert Projects.get_project(project.id).name == "After"
    assert Projects.get_project(project.id).color == "#12ABEF"
    assert has_element?(view, "#project-selector-name", "After")
    assert has_element?(view, "#select-list-#{task_list.id}[aria-current='page']")
    refute_patched(view, path)
  end

  test "failed edit keeps modal, List route, and Task rows unchanged", %{conn: conn} do
    project = project_fixture(%{name: "Before"})
    task_list = list_fixture(project)
    task = task_fixture(project, task_list, %{title: "Keep me"})
    path = ~p"/projects/#{project.id}/lists/#{task_list.id}"
    {:ok, view, _html} = live(conn, path)
    view |> element("#project-edit-button") |> render_click()

    view
    |> form("#project-form",
      project: %{name: "Changed", description: "Draft", icon: "briefcase", color: "BAD"}
    )
    |> render_submit()

    assert has_element?(view, "#project-modal #project-form")
    assert has_element?(view, "#project-color[value='BAD']")
    assert has_element?(view, "#project-colors [data-role='field-error']")
    assert has_element?(view, "#project-selector-name", "Before")
    assert has_element?(view, "#select-list-#{task_list.id}[aria-current='page']")
    assert has_element?(view, "#task-#{task.id}")
    assert Projects.get_project(project.id).name == "Before"
    refute_patched(view, path)
  end

  test "preset swatch fills the editable color and remains selected", %{conn: conn} do
    {:ok, view, _html} = live(conn, ~p"/")
    view |> element("#new-project-button") |> render_click()
    view |> element("#project-color-preset-emerald") |> render_click()

    assert has_element?(view, "#project-color[value='10B981']")
    assert has_element?(view, "#project-color-preset-emerald[aria-pressed='true']")
  end

  test "a queued preset event after cancellation leaves the editor closed", %{conn: conn} do
    {:ok, view, _html} = live(conn, ~p"/")
    view |> element("#new-project-button") |> render_click()
    view |> element("#project-cancel-button") |> render_click()

    render_click(view, "select_project_color", %{"color" => "10B981"})

    refute has_element?(view, "#project-modal")
  end

  test "an unsupported icon receives a field error without unsafe icon rendering", %{conn: conn} do
    {:ok, view, _html} = live(conn, ~p"/")
    view |> element("#new-project-button") |> render_click()

    render_change(view, "validate_project", %{
      "project" => %{"name" => "Project", "icon" => "unsupported", "color" => "6366F1"}
    })

    assert has_element?(view, "#project-icons [data-role='field-error']")
    refute has_element?(view, ".hero-unsupported")
  end

  test "stale edit target becomes unavailable on submission", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")
    view |> element("#project-edit-button") |> render_click()

    Taskman.Repo.delete!(project)

    render_submit(view, "save_project", %{
      "project" => %{"name" => "Changed", "color" => "6366F1", "icon" => "briefcase"}
    })

    assert has_element?(view, "#project-unavailable")
    refute has_element?(view, "#project-form")
  end

  test "invalid submission against a removed edit target shows unavailable", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")
    view |> element("#project-edit-button") |> render_click()
    assert has_element?(view, "#project-form")

    Taskman.Repo.delete!(project)

    render_submit(view, "save_project", %{
      "project" => %{"name" => "", "color" => "BAD", "icon" => "briefcase"}
    })

    assert has_element?(view, "#project-unavailable")
    refute has_element?(view, "#project-form")
  end

  test "a valid pending Task edit flushes before choosing another Project", %{conn: conn} do
    source = project_fixture(%{})
    destination = project_fixture(%{})
    task = task_fixture(source, %{title: "Before"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{source.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "After"})
    |> render_change(%{"_target" => ["task", "title"]})

    view |> element("#project-selector-toggle") |> render_click()
    view |> element("#select-project-#{destination.id}") |> render_click()

    assert_patch(view, ~p"/projects/#{destination.id}")
    assert Taskman.Tasks.get_task_for_project(source, task.id).title == "After"
  end

  test "pristine Project form follows updates, dirty form retains its values", %{conn: conn} do
    project = project_fixture(%{name: "Before", color: "#12ABEF"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")
    view |> element("#project-edit-button") |> render_click()

    assert {:ok, current} = Projects.update_project(project, %{name: "External"})
    render(view)
    assert has_element?(view, "#project-name[value='External']")

    view
    |> form("#project-form", project: %{name: "Mine", color: "12ABEF", icon: "briefcase"})
    |> render_change(%{"_target" => ["project", "name"]})

    assert {:ok, _current} =
             Projects.update_project(current, %{description: "External description"})

    render(view)
    assert has_element?(view, "#project-name[value='Mine']")
    assert has_element?(view, "#project-selector-description", "External description")
  end
end
