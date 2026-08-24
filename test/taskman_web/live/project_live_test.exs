defmodule TaskmanWeb.ProjectLiveTest do
  use TaskmanWeb.ConnCase, async: true

  import Phoenix.LiveViewTest
  import Taskman.ProjectsFixtures
  import Taskman.TasksFixtures

  alias Taskman.Tasks

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

  test "Project patch navigation resets Task rows by their generated stream IDs", %{conn: conn} do
    project_a = project_fixture(%{})
    task_a = task_fixture(project_a, %{title: "Project A Task"})
    project_b = project_fixture(%{})
    task_b = task_fixture(project_b, %{title: "Project B Task"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project_a.id}")

    assert has_element?(view, "#tasks > #tasks-#{task_a.id}")
    refute has_element?(view, "#tasks > #tasks-#{task_b.id}")

    view |> element("#project-#{project_b.id}") |> render_click()

    assert_patch(view, ~p"/projects/#{project_b.id}")
    refute has_element?(view, "#tasks > #tasks-#{task_a.id}")
    refute has_element?(view, "#task-#{task_a.id}")
    assert has_element?(view, "#tasks > #tasks-#{task_b.id}")
    assert has_element?(view, "#task-#{task_b.id}")
  end

  test "opens the canonical Task modal from the row and preserves the list", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Editable", description: "Context"})
    sibling = task_fixture(project, %{title: "Sibling"})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    view |> element("#open-task-#{task.id}") |> render_click()

    assert_patch(view, ~p"/projects/#{project.id}/tasks/#{task.id}")
    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-modal-close[aria-label='Close dialog']")
    assert has_element?(view, "#task-form")
    assert has_element?(view, "#task-title[value='Editable']")
    assert has_element?(view, "#task-description")
    assert has_element?(view, "#task-#{sibling.id}")
    refute has_element?(view, "#close-task")
  end

  test "direct canonical Task URL renders the same modal", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Direct"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#task-form")
    assert has_element?(view, "#task-#{task.id}")
  end

  test "found Task renders truthful detail regions without unavailable operations", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Write launch checklist"})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    assert has_element?(
             view,
             "#taskman-workspace[phx-hook='TaskmanWeb.ProjectLive.TaskDetailLayout']"
           )

    assert has_element?(view, "#task-modal-content[data-size='wide']")
    assert has_element?(view, "#task-detail-layout[data-has-hierarchy='false']")

    assert has_element?(
             view,
             "#task-hierarchy-toggle[aria-controls='task-hierarchy'][aria-expanded='false'][aria-label='Expand task hierarchy']"
           )

    assert has_element?(
             view,
             "#task-hierarchy [role='tree'] [role='treeitem'][aria-current='true']",
             "Write launch checklist"
           )

    assert has_element?(view, "#task-hierarchy-empty", "No parent or child Tasks")

    assert has_element?(
             view,
             "#task-activity-empty",
             "No activity has been recorded for this Task."
           )

    assert has_element?(
             view,
             "#task-sessions-empty",
             "No Agent Sessions are associated with this Task."
           )

    refute has_element?(view, "#task-sessions button")
    refute has_element?(view, "#task-detail-layout [data-session-row]")
  end

  test "invalid Task URLs preserve the selected Project list in a modal not-found state", %{
    conn: conn
  } do
    project = project_fixture(%{})
    visible = task_fixture(project, %{title: "Visible"})
    other_project = project_fixture(%{})
    other = task_fixture(other_project, %{title: "Secret"})

    for task_id <- ["not-an-id", "999999999", Integer.to_string(other.id)] do
      {:ok, view, _html} = live(conn, "/projects/#{project.id}/tasks/#{task_id}")
      assert has_element?(view, "#task-modal-content[aria-labelledby='task-modal-title']")
      assert has_element?(view, "#task-modal-title")
      assert has_element?(view, "#task-not-found")
      assert has_element?(view, "#task-#{visible.id}")
      refute has_element?(view, "#task-form")
      refute has_element?(view, "#task-detail-layout")
      assert has_element?(view, "#task-modal-content[data-size='default']")
      refute has_element?(view, "#task-#{other.id}")
    end
  end

  test "autosaves the targeted Task field and refreshes the streamed row", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Before", status: :pending, priority: :none})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: "After"})
    |> render_change(%{"_target" => ["task", "title"]})

    _ = :sys.get_state(view.pid)

    assert Tasks.get_task_for_project(project, task.id).title == "After"
    assert has_element?(view, "#task-#{task.id}", "After")

    view
    |> form("#task-form", task: %{status: "in_review"})
    |> render_change(%{"_target" => ["task", "status"]})

    assert Tasks.get_task_for_project(project, task.id).status == :in_review
    assert has_element?(view, "#task-status-#{task.id}", "In Review")
  end

  test "immediately persists priority and refreshes the streamed row", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{priority: :none})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{priority: "urgent"})
    |> render_change(%{"_target" => ["task", "priority"]})

    assert Tasks.get_task_for_project(project, task.id).priority == :urgent
    assert has_element?(view, "#task-priority-#{task.id}", "Urgent")
    assert has_element?(view, "#task-priority option[selected][value='urgent']")
  end

  test "immediately persists and clears the due date-time", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{due_at: nil})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{due_at: "2026-08-03T16:00"})
    |> render_change(%{"_target" => ["task", "due_at"]})

    assert Tasks.get_task_for_project(project, task.id).due_at == ~N[2026-08-03 16:00:00]
    assert has_element?(view, "#task-due-at[value='2026-08-03T16:00']")

    view
    |> form("#task-form", task: %{due_at: ""})
    |> render_change(%{"_target" => ["task", "due_at"]})

    assert Tasks.get_task_for_project(project, task.id).due_at == nil
    assert has_element?(view, "#task-due-at[value='']")
  end

  test "an invalid draft does not block another field from saving", %{conn: conn} do
    project = project_fixture(%{})
    task = task_fixture(project, %{title: "Valid", status: :pending})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/#{task.id}")

    view
    |> form("#task-form", task: %{title: ""})
    |> render_change(%{"_target" => ["task", "title"]})

    assert has_element?(view, "#task-form [data-role='field-error']")
    assert Tasks.get_task_for_project(project, task.id).title == "Valid"

    view
    |> form("#task-form", task: %{status: "in_review"})
    |> render_change(%{"_target" => ["task", "status"]})

    updated = Tasks.get_task_for_project(project, task.id)
    assert updated.title == "Valid"
    assert updated.status == :in_review
    assert has_element?(view, "#task-save-status[data-state='not_saved']")
  end

  test "populated Task list renders column headers before its Task stream", %{conn: conn} do
    project = project_fixture(%{})
    _task = task_fixture(project, %{})

    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}")

    assert has_element?(view, "#task-table-header + #tasks")
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

  test "canceling a valid new Task draft does not persist it", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    task_params = %{
      title: "Draft only",
      description: "Keep this draft client-side",
      status: "in_review",
      priority: "high",
      due_at: "2026-08-04T09:30"
    }

    view
    |> form("#task-form", task: task_params)
    |> render_change()

    assert Tasks.list_tasks_for_project(project) == []

    view |> element("#cancel-task") |> render_click()

    assert_patch(view, ~p"/projects/#{project.id}")
    refute has_element?(view, "#task-modal")
    assert Tasks.list_tasks_for_project(project) == []
  end

  test "new Task form exposes every editable field and gates creation on validity", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    assert has_element?(view, "#task-title")
    assert has_element?(view, "#task-description")
    assert has_element?(view, "#task-status option[selected][value='pending']")
    assert has_element?(view, "#task-priority option[selected][value='none']")
    assert has_element?(view, "#task-due-at[value='']")
    assert has_element?(view, "#create-task[disabled]")

    valid_params = %{
      title: "Ready to create",
      description: "",
      status: "pending",
      priority: "none",
      due_at: ""
    }

    view
    |> form("#task-form", task: valid_params)
    |> render_change()

    refute has_element?(view, "#create-task[disabled]")

    view
    |> form("#task-form", task: Map.put(valid_params, :title, ""))
    |> render_change()

    assert has_element?(view, "#create-task[disabled]")
    assert has_element?(view, "#task-form [data-role='field-error']")
  end

  test "creates every Task field explicitly and closes the modal", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    task_params = %{
      title: "Ship complete creation",
      description: "All details supplied up front",
      status: "in_progress",
      priority: "urgent",
      due_at: "2026-08-03T16:00"
    }

    view
    |> form("#task-form", task: task_params)
    |> render_change()

    refute has_element?(view, "#create-task[disabled]")

    view
    |> form("#task-form", task: task_params)
    |> render_submit()

    assert_patch(view, ~p"/projects/#{project.id}")
    refute has_element?(view, "#task-modal")

    [task] = Tasks.list_tasks_for_project(project)
    assert task.title == "Ship complete creation"
    assert task.description == "All details supplied up front"
    assert task.status == :in_progress
    assert task.priority == :urgent
    assert task.due_at == ~N[2026-08-03 16:00:00]
    assert has_element?(view, "#task-#{task.id}")
    assert has_element?(view, "#task-status-#{task.id}", "In Progress")
    assert has_element?(view, "#task-priority-#{task.id}", "Urgent")
    assert has_element?(view, "#tasks-empty.hidden.only\\:block")
  end

  test "direct invalid submission preserves the complete draft", %{conn: conn} do
    project = project_fixture(%{})
    {:ok, view, _html} = live(conn, ~p"/projects/#{project.id}/tasks/new")

    invalid_params = %{
      title: "",
      description: "Keep this draft",
      status: "in_review",
      priority: "high",
      due_at: "2026-08-04T09:30"
    }

    view
    |> form("#task-form", task: invalid_params)
    |> render_submit()

    assert has_element?(view, "#task-modal")
    assert has_element?(view, "#create-task[disabled]")
    assert has_element?(view, "#task-form [data-role='field-error']")
    assert has_element?(view, "#task-description", "Keep this draft")
    assert has_element?(view, "#task-status option[selected][value='in_review']")
    assert has_element?(view, "#task-priority option[selected][value='high']")
    assert has_element?(view, "#task-due-at[value='2026-08-04T09:30']")
    assert Tasks.list_tasks_for_project(project) == []
  end
end
