defmodule TaskmanWeb.ProjectLiveMoveTaskTest do
  use TaskmanWeb.ConnCase, async: false

  import Phoenix.LiveViewTest
  import Taskman.ListsFixtures
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.{Repo, Tasks}

  test "filters destinations case-insensitively by their complete path", %{conn: conn} do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    launch = list_fixture(project, planning, %{name: "Launch"})
    archive = list_fixture(project, nil, %{name: "Archive"})
    task = task_fixture(project, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()

    view
    |> element("#move-task-search-#{task.id}")
    |> render_keyup(%{"value" => "LaUnCh"})

    assert has_element?(
             view,
             "#move-task-option-list-#{launch.id}[aria-label='Planning / Launch']"
           )

    refute has_element?(view, "#move-task-option-project-#{project.id}")
    refute has_element?(view, "#move-task-option-list-#{planning.id}")
    refute has_element?(view, "#move-task-option-list-#{archive.id}")
  end

  test "destination combobox starts collapsed, opens on click, and closes on selection", %{
    conn: conn
  } do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()

    assert has_element?(view, "#move-task-search-#{task.id}[aria-expanded='false']")
    refute has_element?(view, "#move-task-options-#{task.id}")

    view |> element("#move-task-search-#{task.id}") |> render_click()

    assert has_element?(view, "#move-task-search-#{task.id}[aria-expanded='true']")
    assert has_element?(view, "#move-task-option-list-#{planning.id}")

    view |> element("#move-task-option-list-#{planning.id}") |> render_click()

    assert has_element?(
             view,
             "#move-task-search-#{task.id}[aria-expanded='false'][value='Planning']"
           )

    refute has_element?(view, "#move-task-options-#{task.id}")
  end

  test "reopening a selected destination filters its query and refreshes persisted location", %{
    conn: conn
  } do
    project = project_fixture(%{})
    inbox = list_fixture(project, nil, %{name: "Inbox"})
    planning = list_fixture(project, nil, %{name: "Planning"})
    archive = list_fixture(project, nil, %{name: "Archive"})
    task = task_fixture(project, inbox, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}?include_children=true")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    assert {:ok, _moved} = Tasks.move_task(project, task, planning)

    view |> element("#move-task-option-list-#{planning.id}") |> render_click()

    assert Tasks.get_task_for_project(project, task.id).list_id == planning.id
    assert has_element?(view, "#move-task-submit-#{task.id}[disabled]")

    persisted_task = Tasks.get_task_for_project(project, task.id)
    assert {:ok, _moved} = Tasks.move_task(project, persisted_task, inbox)

    view |> element("#move-task-search-#{task.id}") |> render_click()

    assert has_element?(
             view,
             "#move-task-search-#{task.id}[aria-expanded='true'][value='Planning']"
           )

    assert has_element?(
             view,
             "#move-task-option-list-#{planning.id}[aria-selected='true'][data-current-location='false']"
           )

    refute has_element?(view, "#move-task-option-project-#{project.id}")
    refute has_element?(view, "#move-task-option-list-#{inbox.id}")
    refute has_element?(view, "#move-task-option-list-#{archive.id}")
    refute has_element?(view, "#move-task-submit-#{task.id}[disabled]")
    assert Tasks.get_task_for_project(project, task.id).list_id == inbox.id
  end

  test "lists project and nested List destinations in tree order", %{conn: conn} do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    launch = list_fixture(project, planning, %{name: "Launch"})
    archive = list_fixture(project, nil, %{name: "Archive"})
    task = task_fixture(project, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")
    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()

    assert has_element?(
             view,
             "#move-task-option-project-#{project.id}[aria-label='Project · #{project.name}']"
           )

    option_ids =
      view
      |> render()
      |> LazyHTML.from_fragment()
      |> LazyHTML.query("#move-task-options-#{task.id} [role='option']")
      |> LazyHTML.to_tree(skip_whitespace_nodes: true)
      |> Enum.map(fn {_tag, attributes, _children} ->
        {"id", id} = List.keyfind(attributes, "id", 0)
        id
      end)

    assert option_ids == [
             "move-task-option-project-#{project.id}",
             "move-task-option-list-#{planning.id}",
             "move-task-option-list-#{launch.id}",
             "move-task-option-list-#{archive.id}"
           ]
  end

  test "moves a Task from a row and removes it from the selected direct List", %{conn: conn} do
    project = project_fixture(%{})
    inbox = list_fixture(project, nil, %{name: "Inbox"})
    planning = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, inbox, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/lists/#{inbox.id}")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{planning.id}") |> render_click()
    view |> element("#move-task-submit-#{task.id}") |> render_click()

    assert Tasks.get_task_for_project(project, task.id).list_id == planning.id
    refute has_element?(view, "#tasks-#{task.id}")
    refute has_element?(view, "#move-task-#{task.id}")
  end

  test "moves from Task detail while keeping the detail route open", %{conn: conn} do
    project = project_fixture(%{})
    inbox = list_fixture(project, nil, %{name: "Inbox"})
    planning = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, inbox, %{title: "Move me"})
    task_path = ~p"/projects/#{project.id}/lists/#{inbox.id}/tasks/#{task.id}"

    {:ok, view, _html} = live(conn, task_path)

    view |> element("#move-task-detail-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{planning.id}") |> render_click()
    view |> element("#move-task-submit-#{task.id}") |> render_click()

    assert Tasks.get_task_for_project(project, task.id).list_id == planning.id
    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-form")
    refute has_element?(view, "#tasks-#{task.id}")
    refute_patched(view, task_path)
    assert has_element?(view, "#task-save-status[data-state='saved']")

    view
    |> form("#task-form", task: %{priority: "urgent"})
    |> render_change(%{"_target" => ["task", "priority"]})

    assert Tasks.get_task_for_project(project, task.id).priority == :urgent
    assert has_element?(view, "#task-priority option[selected][value='urgent']")
    assert has_element?(view, "#task-save-status[data-state='saved']")
    refute_patched(view, task_path)
  end

  test "gives the Task modal sole Escape ownership while its move popover is open", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Move me"})
    task_path = ~p"/projects/#{project.id}/tasks/#{task.id}"

    {:ok, view, _html} = live(conn, task_path)

    view |> element("#move-task-detail-button-#{task.id}") |> render_click()

    assert has_element?(
             view,
             "#task-modal-content[phx-window-keydown*='cancel_move_task']"
           )

    assert has_element?(view, "#move-task-#{task.id}")
    refute has_element?(view, "#move-task-#{task.id}[phx-window-keydown]")

    view |> element("#task-modal-content") |> render_keydown(%{"key" => "Escape"})

    refute has_element?(view, "#move-task-#{task.id}")
    assert has_element?(view, "#task-modal")
    refute_patched(view, task_path)
  end

  test "detail Move action pushes its focus before opening the popover", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    [open_actions] =
      view
      |> render()
      |> LazyHTML.from_fragment()
      |> LazyHTML.query("#move-task-detail-button-#{task.id}")
      |> LazyHTML.attribute("phx-click")

    assert open_actions =~ ~r/^\[\["push_focus".*"push".*"open_move_task"/
  end

  test "retains an included descendant row and refreshes its location label after a move", %{
    conn: conn
  } do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Work"})
    inbox = list_fixture(project, root, %{name: "Inbox"})
    planning = list_fixture(project, root, %{name: "Planning"})
    task = task_fixture(project, inbox, %{title: "Move me"})

    {:ok, view, _html} =
      live(conn, ~p"/projects/#{project.id}/lists/#{root.id}?include_children=true")

    assert has_element?(
             view,
             "#task-location-cell-#{task.id}[aria-label='Location: Work / Inbox']"
           )

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{planning.id}") |> render_click()
    view |> element("#move-task-submit-#{task.id}") |> render_click()

    assert Tasks.get_task_for_project(project, task.id).list_id == planning.id
    assert has_element?(view, "#tasks-#{task.id}")

    assert has_element?(
             view,
             "#task-location-cell-#{task.id}[aria-label='Location: Work / Planning']"
           )
  end

  test "refreshes an open move surface after a List rename", %{conn: conn} do
    project = project_fixture(%{})
    root = list_fixture(project, nil, %{name: "Planning"})
    child = list_fixture(project, root, %{name: "Launch"})
    task = task_fixture(project, child, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}?include_children=true")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-project-#{project.id}") |> render_click()

    view
    |> element("#move-task-search-#{task.id}")
    |> render_keyup(%{"value" => ""})

    assert has_element?(
             view,
             "#move-task-option-list-#{child.id}[aria-label='Planning / Launch']"
           )

    view |> element("#toggle-project-#{project.id}") |> render_click()
    view |> element("#rename-list-#{root.id}") |> render_click()

    view
    |> form("#list-rename-form-#{root.id}", list: %{name: "Roadmap"})
    |> render_submit()

    assert has_element?(view, "#move-task-#{task.id}")

    assert has_element?(
             view,
             "#move-task-option-list-#{child.id}[aria-label='Roadmap / Launch']"
           )

    assert has_element?(
             view,
             "#move-task-option-project-#{project.id}[aria-selected='false']"
           )
  end

  test "keeps one move surface open and cancellation clears its transient state", %{conn: conn} do
    project = project_fixture(%{})
    first = task_fixture(project, %{title: "First"})
    second = task_fixture(project, %{title: "Second"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#move-task-row-button-#{first.id}") |> render_click()
    assert has_element?(view, "#move-task-#{first.id}")

    view |> element("#move-task-row-button-#{second.id}") |> render_click()
    refute has_element?(view, "#move-task-#{first.id}")
    assert has_element?(view, "#move-task-#{second.id}")

    view |> element("#cancel-move-task-#{second.id}") |> render_click()

    refute has_element?(view, "#move-task-#{second.id}")
  end

  test "canceling and reopening clears the move query, destination, and error", %{conn: conn} do
    project = project_fixture(%{})
    foreign_project = project_fixture(%{})
    foreign_list = list_fixture(foreign_project, nil, %{name: "Foreign"})
    task = task_fixture(project, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()

    view
    |> element("#move-task-search-#{task.id}")
    |> render_keyup(%{"value" => "Project"})

    render_click(view, "select_move_destination", %{"destination" => "list:#{foreign_list.id}"})
    render_click(view, "submit_move_task")

    assert has_element?(view, "#move-task-error-#{task.id}[role='alert']")

    view |> element("#cancel-move-task-#{task.id}") |> render_click()
    view |> element("#move-task-row-button-#{task.id}") |> render_click()

    assert has_element?(view, "#move-task-search-#{task.id}[value='']")
    refute has_element?(view, "#move-task-error-#{task.id}")
    assert has_element?(view, "#move-task-search-#{task.id}[aria-expanded='false']")
    refute has_element?(view, "#move-task-options-#{task.id}")

    view |> element("#move-task-search-#{task.id}") |> render_click()

    assert has_element?(
             view,
             "#move-task-option-project-#{project.id}[aria-selected='false'][data-current-location='true']"
           )
  end

  test "keeps the row Move Task error visible when the Task is deleted after opening", %{
    conn: conn
  } do
    project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{destination.id}") |> render_click()
    Repo.delete!(task)

    view |> element("#move-task-submit-#{task.id}") |> render_click()

    assert has_element?(view, "#tasks-#{task.id}")
    assert has_element?(view, "#move-task-#{task.id}")
    assert has_element?(view, "#move-task-error-#{task.id}[role='alert']")
  end

  test "keeps the row Move Task error visible when its selected List is deleted", %{conn: conn} do
    project = project_fixture(%{})
    destination = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{destination.id}") |> render_click()
    Repo.delete!(destination)

    view |> element("#move-task-submit-#{task.id}") |> render_click()

    assert Tasks.get_task_for_project(project, task.id).list_id == nil
    assert has_element?(view, "#tasks-#{task.id}")
    assert has_element?(view, "#move-task-#{task.id}")
    assert has_element?(view, "#move-task-error-#{task.id}[role='alert']")
  end

  test "keeps row opening available while the Move Task control is separate", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Open me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    assert has_element?(view, "#task-#{task.id}.pointer-events-none")

    assert has_element?(
             view,
             "#task-actions-#{task.id} #move-task-row-button-#{task.id}.pointer-events-auto"
           )

    view |> element("#open-task-#{task.id}") |> render_click()

    assert_patch(view, ~p"/projects/#{project.id}/tasks/#{task.id}")
    assert has_element?(view, "#task-modal")
  end

  test "rejects a destination that became the Task current location before submission", %{
    conn: conn
  } do
    project = project_fixture(%{})
    inbox = list_fixture(project, nil, %{name: "Inbox"})
    planning = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, inbox, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}?include_children=true")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{planning.id}") |> render_click()
    assert {:ok, _moved} = Tasks.move_task(project, task, planning)

    view |> element("#move-task-submit-#{task.id}") |> render_click()

    assert Tasks.get_task_for_project(project, task.id).list_id == planning.id
    assert has_element?(view, "#move-task-#{task.id}")
    assert has_element?(view, "#move-task-error-#{task.id}[role='alert']")
  end

  test "search refreshes a move surface from the Task persisted location", %{conn: conn} do
    project = project_fixture(%{})
    inbox = list_fixture(project, nil, %{name: "Inbox"})
    planning = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, inbox, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}?include_children=true")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{planning.id}") |> render_click()
    assert {:ok, _moved} = Tasks.move_task(project, task, planning)

    view
    |> element("#move-task-search-#{task.id}")
    |> render_keyup(%{"value" => "Planning"})

    assert has_element?(
             view,
             "#move-task-option-list-#{planning.id}[data-current-location='true'][aria-selected='true']"
           )

    assert has_element?(view, "#move-task-submit-#{task.id}[disabled]")
  end

  test "an unchanged move rebuilds its current destination before showing the error", %{
    conn: conn
  } do
    project = project_fixture(%{})
    inbox = list_fixture(project, nil, %{name: "Inbox"})
    planning = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, inbox, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}?include_children=true")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{planning.id}") |> render_click()
    assert {:ok, _moved} = Tasks.move_task(project, task, planning)

    view |> element("#move-task-submit-#{task.id}") |> render_click()

    assert has_element?(view, "#move-task-error-#{task.id}[role='alert']")

    view |> element("#move-task-search-#{task.id}") |> render_click()

    assert has_element?(
             view,
             "#move-task-option-list-#{planning.id}[data-current-location='true'][aria-selected='true']"
           )

    assert has_element?(view, "#move-task-submit-#{task.id}[disabled]")
  end

  test "rejects a forced cross-Project List destination", %{conn: conn} do
    project = project_fixture(%{})
    foreign_project = project_fixture(%{})
    foreign_list = list_fixture(foreign_project, nil, %{name: "Foreign"})
    task = task_fixture(project, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")
    view |> element("#move-task-row-button-#{task.id}") |> render_click()

    render_click(view, "select_move_destination", %{"destination" => "list:#{foreign_list.id}"})
    render_click(view, "submit_move_task")

    assert Tasks.get_task_for_project(project, task.id).list_id == nil
    assert has_element?(view, "#move-task-#{task.id}")
    assert has_element?(view, "#move-task-error-#{task.id}[role='alert']")
  end

  test "moves a List-owned Task back to the Project root", %{conn: conn} do
    project = project_fixture(%{})
    inbox = list_fixture(project, nil, %{name: "Inbox"})
    task = task_fixture(project, inbox, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/lists/#{inbox.id}")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-project-#{project.id}") |> render_click()
    view |> element("#move-task-submit-#{task.id}") |> render_click()

    assert Tasks.get_task_for_project(project, task.id).list_id == nil
    refute has_element?(view, "#tasks-#{task.id}")
    refute has_element?(view, "#move-task-#{task.id}")
  end

  test "changing destination search invalidates the previous selection", %{conn: conn} do
    project = project_fixture(%{})
    planning = list_fixture(project, nil, %{name: "Planning"})
    task = task_fixture(project, %{title: "Move me"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    view |> element("#move-task-search-#{task.id}") |> render_click()
    view |> element("#move-task-option-list-#{planning.id}") |> render_click()

    view
    |> element("#move-task-search-#{task.id}")
    |> render_keyup(%{"value" => "No matching location"})

    assert has_element?(view, "#move-task-no-results-#{task.id}")
    refute has_element?(view, "#move-task-options-#{task.id} [aria-selected='true']")
    assert has_element?(view, "#move-task-submit-#{task.id}[disabled]")
  end

  test "changing Project route clears an open move interaction", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Move me"})
    destination_project = project_fixture(%{})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#move-task-row-button-#{task.id}") |> render_click()
    assert has_element?(view, "#move-task-#{task.id}")

    view |> element("#select-project-#{destination_project.id}") |> render_click()

    assert_patch(view, ~p"/projects/#{destination_project.id}")
    assert has_element?(view, "#project-#{destination_project.id}[aria-current='page']")
    refute has_element?(view, "#move-task-#{task.id}")
  end
end
