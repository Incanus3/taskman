defmodule TaskmanWeb.GlobalTaskSearchTest do
  use TaskmanWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import Taskman.AccountsFixtures
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Repo
  alias Taskman.Tasks
  alias TaskmanWeb.GlobalTaskSearch

  setup %{conn: conn}, do: {:ok, conn: log_in_user(conn, user_fixture())}

  test "the shared header offers desktop and narrow search controls with a blank prompt", %{
    conn: conn
  } do
    {:ok, view, _html} = live(conn, ~p"/account/settings")

    assert has_element?(
             view,
             "#global-task-search-desktop #global-task-search-input[role='combobox'][aria-label='Search Tasks']"
           )

    assert has_element?(view, "#global-task-search-mobile-trigger[aria-label='Search Tasks']")

    assert has_element?(
             view,
             "#account-settings-link[aria-label='Account settings'][title='Account settings'] .hero-cog-6-tooth"
           )

    assert has_element?(
             view,
             "#account-sign-out-link[aria-label='Sign out'][title='Sign out'] .hero-arrow-right-start-on-rectangle"
           )

    view |> element("#global-task-search-input") |> render_focus()

    assert has_element?(
             view,
             "#global-task-search-prompt[role='status']",
             "Search Tasks by ID or title"
           )

    assert has_element?(
             view,
             "#global-task-search-loading[role='status'][class*='group-[.phx-change-loading]:block']",
             "Searching Tasks"
           )

    refute has_element?(view, "#global-task-search-results[role='listbox']")
    assert has_element?(view, "#global-task-search-input[aria-expanded='false']")
    refute has_element?(view, "#global-task-search-input[aria-controls]")

    view |> element("#global-task-search-mobile-trigger") |> render_click()
    assert has_element?(view, "#global-task-search-mobile-panel #global-task-search-mobile-input")
    view |> element("#global-task-search-mobile-close") |> render_click()
    refute has_element?(view, "#global-task-search-mobile-panel")
  end

  test "results show status, priority, and owning location; changing query replaces them", %{
    conn: conn
  } do
    project = project_fixture(%{name: "Alpha"})
    list = list_fixture(project, %{name: "Launch"})
    task = task_fixture(project, list, %{title: "Publish release", priority: :urgent})
    {:ok, view, _html} = live(conn, ~p"/account/settings")

    search(view, "Publish")
    assert has_element?(view, "#global-task-search-results[role='listbox']")
    refute has_element?(view, "[aria-live] #global-task-search-results")

    assert has_element?(
             view,
             "#global-task-search-input[aria-expanded='true'][aria-controls='global-task-search-results']"
           )

    assert has_element?(view, "#global-task-search-option-#{task.id}", "Publish release")
    assert has_element?(view, "#global-task-search-option-#{task.id}", "Alpha / Launch")
    assert has_element?(view, "#global-task-search-option-#{task.id}", "Urgent")
    assert has_element?(view, "#global-task-search-option-#{task.id}", "Pending")

    search(view, "unmatched query")
    assert has_element?(view, "#global-task-search-no-match", "No Tasks found")
    refute has_element?(view, "#global-task-search-results[role='listbox']")
    assert has_element?(view, "#global-task-search-input[aria-expanded='false']")
    refute has_element?(view, "#global-task-search-option-#{task.id}")

    search(view, "  ")
    assert has_element?(view, "#global-task-search-prompt")
    refute has_element?(view, "#global-task-search-results[role='listbox']")
  end

  test "the navbar uses the same whitespace-term matching as the Tasks context", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Long query target"})
    {:ok, view, _html} = live(conn, ~p"/account/settings")

    search(view, String.duplicate(" ", 201) <> Integer.to_string(task.id))
    assert has_element?(view, "#global-task-search-option-#{task.id}")
  end

  test "arrow selection and Enter navigate without changing the Task", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Keyboard jump"})
    {:ok, view, _html} = live(conn, ~p"/account/settings")

    search(view, "Keyboard jump")
    view |> element("#global-task-search-input") |> render_keydown(%{"key" => "ArrowDown"})

    assert has_element?(
             view,
             "#global-task-search-input[aria-activedescendant='global-task-search-option-#{task.id}']"
           )

    view |> element("#global-task-search-input") |> render_keydown(%{"key" => "Enter"})
    assert_redirect(view, ~p"/projects/#{project.id}/tasks/#{task.id}")
    assert Tasks.get_task_for_project(project, task.id).lock_version == task.lock_version
  end

  test "Escape clears results and the narrow panel closes to its trigger", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Escape jump"})
    {:ok, view, _html} = live(conn, ~p"/account/settings")

    view |> element("#global-task-search-mobile-trigger") |> render_click()

    view
    |> form("#global-task-search-mobile-form", %{"search" => %{"query" => "Escape jump"}})
    |> render_change()

    assert has_element?(view, "#global-task-search-mobile-option-#{task.id}")
    view |> element("#global-task-search-mobile-input") |> render_keydown(%{"key" => "Escape"})
    refute has_element?(view, "#global-task-search-mobile-panel")
    assert has_element?(view, "#global-task-search-mobile-trigger[aria-expanded='false']")

    view |> element("#global-task-search-mobile-trigger") |> render_click()
    assert has_element?(view, "#global-task-search-mobile-prompt")
    refute has_element?(view, "#global-task-search-mobile-option-#{task.id}")
  end

  @tag :capture_log
  test "a search failure offers retry and preserves the current page", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Retry jump"})
    {:ok, view, _html} = live(conn, ~p"/account/settings")

    Phoenix.LiveView.send_update(view.pid, GlobalTaskSearch,
      id: "global-task-search",
      search_tasks: fn _query -> raise "search unavailable" end
    )

    search(view, "Retry jump")

    assert has_element?(view, "#global-task-search-error[role='alert']", "Task search failed")
    refute has_element?(view, "#global-task-search-results[role='listbox']")
    assert has_element?(view, "#account-settings")

    Phoenix.LiveView.send_update(view.pid, GlobalTaskSearch,
      id: "global-task-search",
      search_tasks: &Tasks.search_tasks/1
    )

    view |> element("#global-task-search-error button", "Retry") |> render_click()
    assert has_element?(view, "#global-task-search-option-#{task.id}")
  end

  @tag :capture_log
  test "a failed result recheck stays on the page, preserves Task state, and can retry", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Recheck jump"})
    {:ok, view, _html} = live(conn, ~p"/account/settings")

    search(view, "Recheck jump")

    Phoenix.LiveView.send_update(view.pid, GlobalTaskSearch,
      id: "global-task-search",
      search_tasks: fn _query -> raise "search unavailable" end
    )

    view |> element("#global-task-search-input") |> render_keydown(%{"key" => "ArrowDown"})
    view |> element("#global-task-search-input") |> render_keydown(%{"key" => "Enter"})
    assert has_element?(view, "#global-task-search-error[role='alert']")
    refute has_element?(view, "#global-task-search-input[aria-activedescendant]")
    assert has_element?(view, "#account-settings")
    assert Tasks.get_task_for_project(project, task.id).lock_version == task.lock_version

    Phoenix.LiveView.send_update(view.pid, GlobalTaskSearch,
      id: "global-task-search",
      search_tasks: &Tasks.search_tasks/1
    )

    view |> element("#global-task-search-error button", "Retry") |> render_click()
    assert has_element?(view, "#global-task-search-option-#{task.id}")
  end

  @tag :capture_log
  test "a Project lookup failure after the recheck stays on the page and offers retry", %{
    conn: conn
  } do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Project lookup jump"})
    {:ok, view, _html} = live(conn, ~p"/account/settings")

    search(view, "Project lookup jump")

    Phoenix.LiveView.send_update(view.pid, GlobalTaskSearch,
      id: "global-task-search",
      get_project: fn _project_id -> raise "project lookup unavailable" end
    )

    view |> element("#global-task-search-input") |> render_keydown(%{"key" => "ArrowDown"})
    view |> element("#global-task-search-input") |> render_keydown(%{"key" => "Enter"})
    assert has_element?(view, "#global-task-search-error[role='alert']")
    refute has_element?(view, "#global-task-search-input[aria-activedescendant]")
    assert has_element?(view, "#account-settings")
    assert Tasks.get_task_for_project(project, task.id).lock_version == task.lock_version

    Phoenix.LiveView.send_update(view.pid, GlobalTaskSearch,
      id: "global-task-search",
      get_project: &Taskman.Projects.get_project/1
    )

    view |> element("#global-task-search-error button", "Retry") |> render_click()
    assert has_element?(view, "#global-task-search-option-#{task.id}")
  end

  test "pointer selection retains a visible same Project List backdrop", %{conn: conn} do
    project = project_fixture(%{})
    parent = list_fixture(project, %{name: "Parent"})
    child = list_fixture(project, parent, %{name: "Child"})
    task = task_fixture(project, child, %{title: "Nested jump"})

    {:ok, view, _html} =
      live(
        conn,
        ~p"/projects/#{project.id}/lists/#{parent.id}?include_children=true&statuses=done"
      )

    search(view, "Nested jump")
    view |> element("#global-task-search-option-#{task.id}") |> render_click()
    assert_redirect(view, ~p"/projects/#{project.id}/lists/#{parent.id}/tasks/#{task.id}")
  end

  test "invisible same Project and cross Project results use their owning locations", %{
    conn: conn
  } do
    project = project_fixture(%{})
    selected = list_fixture(project, %{name: "Selected"})
    owner = list_fixture(project, %{name: "Owner"})
    own_task = task_fixture(project, owner, %{title: "Own location"})
    other_project = project_fixture(%{})
    other_task = task_fixture(other_project, %{title: "Other root"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/lists/#{selected.id}")

    search(view, "Own location")
    view |> element("#global-task-search-option-#{own_task.id}") |> render_click()
    assert_redirect(view, ~p"/projects/#{project.id}/lists/#{owner.id}/tasks/#{own_task.id}")

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/lists/#{selected.id}")
    search(view, "Other root")
    view |> element("#global-task-search-option-#{other_task.id}") |> render_click()
    assert_redirect(view, ~p"/projects/#{other_project.id}/tasks/#{other_task.id}")
  end

  test "selection resolves a Task moved after search", %{conn: conn} do
    project = project_fixture(%{})
    original = list_fixture(project, %{name: "Original"})
    destination = list_fixture(project, %{name: "Destination"})
    task = task_fixture(project, original, %{title: "Moving jump"})
    {:ok, view, _html} = live(conn, ~p"/account/settings")

    search(view, "Moving jump")
    assert {:ok, _} = Tasks.move_task(project, task, destination)
    view |> element("#global-task-search-option-#{task.id}") |> render_click()
    assert_redirect(view, ~p"/projects/#{project.id}/lists/#{destination.id}/tasks/#{task.id}")
  end

  test "a deleted search result reaches the existing Task-not-found view", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Deleted jump"})
    {:ok, view, _html} = live(conn, ~p"/account/settings")

    search(view, "Deleted jump")
    Repo.delete!(task)
    view |> element("#global-task-search-option-#{task.id}") |> render_click()
    path = ~p"/projects/#{project.id}/tasks/#{task.id}"
    assert_redirect(view, path)

    {:ok, destination, _html} = live(conn, path)
    assert has_element?(destination, "#task-not-found")
  end

  test "Task detail keeps navbar search mounted and inert until the modal closes", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Detail task"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#global-task-search[inert] #global-task-search-input")
    assert has_element?(view, "#global-task-search[inert] #global-task-search-mobile-trigger")

    view |> element("#task-modal-close") |> render_click()
    assert has_element?(view, "#global-task-search:not([inert]) #global-task-search-input")
    search(view, "Detail task")
    assert has_element?(view, "#global-task-search-option-#{task.id}")
  end

  test "Task creation availability preserves the mounted search query and results", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Retained query"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    search(view, "Retained query")
    render_patch(view, ~p"/projects/#{project.id}/tasks/new")

    assert has_element?(view, "#task-modal #task-form")

    assert has_element?(
             view,
             "#global-task-search[inert] #global-task-search-input[value='Retained query']"
           )

    assert has_element?(view, "#global-task-search[inert] #global-task-search-option-#{task.id}")

    view |> element("#task-modal-close") |> render_click()

    assert has_element?(
             view,
             "#global-task-search:not([inert]) #global-task-search-input[value='Retained query']"
           )

    assert has_element?(view, "#global-task-search-option-#{task.id}")
    search(view, "another query")
    assert has_element?(view, "#global-task-search-no-match")
  end

  test "Task recovery retains an inert navbar search until input is discarded", %{conn: conn} do
    project = project_fixture(%{})
    lost = list_fixture(project)
    task = task_fixture(project, lost, %{title: "Persisted"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/lists/#{lost.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: ""})
    |> render_change(%{"_target" => ["task", "title"]})

    Repo.delete!(task)
    Repo.delete!(lost)

    send(view.pid, %Taskman.ChangeNotifications.Event{
      entity: :list,
      operation: :updated,
      project_id: project.id,
      entity_id: lost.id,
      lock_version: nil,
      fields: [:name]
    })

    render(view)
    assert has_element?(view, "#task-recovery")
    assert has_element?(view, "#global-task-search[inert] #global-task-search-input")
    assert has_element?(view, "#global-task-search[inert] #global-task-search-mobile-trigger")

    view |> element("#task-recovery-discard") |> render_click()
    assert has_element?(view, "#global-task-search:not([inert]) #global-task-search-input")
  end

  test "a missing Task keeps the mounted navbar search inert", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/999999999")

    assert has_element?(view, "#task-not-found")
    assert has_element?(view, "#global-task-search[inert] #global-task-search-input")
  end

  defp search(view, query) do
    view
    |> form("#global-task-search-form", %{"search" => %{"query" => query}})
    |> render_change()
  end
end
